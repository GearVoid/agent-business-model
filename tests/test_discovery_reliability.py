"""Offline reliability tests for watermark discovery and source health."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import discover_industry  # noqa: E402
import discover_papers  # noqa: E402
import run_pipeline  # noqa: E402


def arxiv_xml(entries: list[tuple[str, str]]) -> str:
    body = "".join(
        "<entry><id>http://arxiv.org/abs/%s</id><title>Perovskite solar cell %s</title>"
        "<summary>perovskite photovoltaics</summary><published>%s</published>"
        "<author><name>Author</name></author></entry>" % (paper_id, paper_id, published)
        for paper_id, published in entries
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'


EMPTY_RSS = b"<?xml version='1.0'?><rss><channel><title>empty</title></channel></rss>"


class PaperWatermarkTests(unittest.TestCase):
    def paper_paths(self, root: Path):
        return (
            patch.object(discover_papers, "CONFIG_PATH", root / "sources.json"),
            patch.object(discover_papers, "STATE_PATH", root / "state-feed.json"),
            patch.object(discover_papers, "FEED_PATH", root / "feed-papers.json"),
            patch.object(discover_papers, "REJECTED_PATH", root / "rejected-papers.json"),
        )

    def write_config(self, root: Path) -> None:
        (root / "sources.json").write_text(json.dumps({"arxiv": {
            "base_url": "https://example.invalid/api", "search_query": "all:perovskite",
            "page_size": 2, "max_pages": 4, "watermark_overlap_days": 0, "type": "paper",
        }}), encoding="utf-8")

    def test_watermark_pages_back_past_one_page_without_duplicate_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_config(root)
            state = {
                "arxiv:old": "2026-07-10",
                "_meta": {"sources": {"arxiv": {"watermark": "2026-07-10T00:00:00Z"}}},
            }
            (root / "state-feed.json").write_text(json.dumps(state), encoding="utf-8")
            pages = {
                0: arxiv_xml([("2607.00001v1", "2026-07-13T12:00:00Z"), ("2607.00002v1", "2026-07-12T12:00:00Z")]),
                2: arxiv_xml([("2607.00003v1", "2026-07-11T12:00:00Z"), ("oldv1", "2026-07-10T00:00:00Z")]),
            }
            with ExitStack() as stack:
                for item in self.paper_paths(root):
                    stack.enter_context(item)
                stack.enter_context(patch.object(discover_papers, "fetch_arxiv_page", side_effect=lambda cfg, start, size: pages[start]))
                stack.enter_context(patch.object(sys, "argv", ["discover_papers.py"]))
                self.assertEqual(discover_papers.main(), 0)
            feed = json.loads((root / "feed-papers.json").read_text(encoding="utf-8"))
            self.assertEqual([item["id"] for item in feed["items"]], ["arxiv:2607.00001", "arxiv:2607.00002", "arxiv:2607.00003"])
            updated = json.loads((root / "state-feed.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["_meta"]["sources"]["arxiv"]["watermark"], "2026-07-13T12:00:00Z")
            self.assertEqual(updated["arxiv:2607.00003"], updated["arxiv:2607.00001"])

    def test_terminal_page_does_not_leak_unseen_entries_before_cutoff(self):
        cfg = {
            "base_url": "https://example.invalid/api",
            "search_query": "all:perovskite",
            "page_size": 3,
            "max_pages": 2,
            "watermark_overlap_days": 7,
        }
        page = arxiv_xml([
            ("2607.00001v1", "2026-07-13T12:00:00Z"),
            ("2607.00002v1", "2026-07-11T12:00:00Z"),
            ("2607.00003v1", "2026-07-09T12:00:00Z"),
        ])
        with patch.object(discover_papers, "fetch_arxiv_page", return_value=page):
            entries, _, scan = discover_papers.scan_arxiv(
                cfg,
                "paper",
                "2026-07-10T00:00:00Z",
                overlap_days=0,
            )

        self.assertEqual(
            [entry["id"] for entry in entries],
            ["arxiv:2607.00001", "arxiv:2607.00002"],
        )
        self.assertEqual(scan["fetched_entry_count"], 3)
        self.assertEqual(scan["entry_count"], 2)
        self.assertEqual(scan["cutoff"], "2026-07-10T00:00:00Z")

    def test_fetch_failure_does_not_advance_watermark_or_replace_old_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_config(root)
            old_state = {"arxiv:old": "2026-07-10", "_meta": {"sources": {"arxiv": {"watermark": "2026-07-10T00:00:00Z"}}}}
            (root / "state-feed.json").write_text(json.dumps(old_state), encoding="utf-8")
            (root / "feed-papers.json").write_text('{"old_feed": true}', encoding="utf-8")
            first = arxiv_xml([("2607.00001v1", "2026-07-13T12:00:00Z"), ("2607.00002v1", "2026-07-12T12:00:00Z")])
            def fetch_page(cfg, start, size):
                if start == 0:
                    return first
                raise OSError("mock network interruption")
            with ExitStack() as stack:
                for item in self.paper_paths(root):
                    stack.enter_context(item)
                stack.enter_context(patch.object(discover_papers, "fetch_arxiv_page", side_effect=fetch_page))
                stack.enter_context(patch.object(sys, "argv", ["discover_papers.py"]))
                self.assertEqual(discover_papers.main(), 1)
            self.assertEqual(json.loads((root / "state-feed.json").read_text(encoding="utf-8")), old_state)
            self.assertEqual(json.loads((root / "feed-papers.json").read_text(encoding="utf-8")), {"old_feed": True})

    def test_legacy_id_only_state_still_deduplicates_before_metadata_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_config(root)
            config_path = root / "sources.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["arxiv"]["page_size"] = 3
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (root / "state-feed.json").write_text(json.dumps({"arxiv:old": "2026-07-01"}), encoding="utf-8")
            page = arxiv_xml([("oldv1", "2026-07-10T00:00:00Z"), ("2607.00004v1", "2026-07-09T00:00:00Z")])
            with ExitStack() as stack:
                for item in self.paper_paths(root):
                    stack.enter_context(item)
                stack.enter_context(patch.object(discover_papers, "fetch_arxiv_page", return_value=page))
                stack.enter_context(patch.object(sys, "argv", ["discover_papers.py"]))
                self.assertEqual(discover_papers.main(), 0)
            feed = json.loads((root / "feed-papers.json").read_text(encoding="utf-8"))
            self.assertEqual([item["id"] for item in feed["items"]], ["arxiv:2607.00004"])
            state = json.loads((root / "state-feed.json").read_text(encoding="utf-8"))
            self.assertIn("_meta", state)

    def test_preview_and_bootstrap_scans_default_to_bounded_recent_windows(self):
        now = discover_papers.parse_timestamp("2026-07-13T12:00:00Z")
        cfg = {"bootstrap_lookback_days": 14, "preview_lookback_days": 3}

        preview, preview_origin = discover_papers.select_scan_watermark(
            cfg, use_state=False, stored_watermark=None, now=now
        )
        bootstrap, bootstrap_origin = discover_papers.select_scan_watermark(
            cfg, use_state=True, stored_watermark=None, now=now
        )
        resumed, resumed_origin = discover_papers.select_scan_watermark(
            cfg, use_state=True, stored_watermark="2026-07-01T00:00:00Z", now=now
        )

        self.assertEqual(preview, "2026-07-10T12:00:00Z")
        self.assertEqual(preview_origin, "preview_lookback_days")
        self.assertEqual(bootstrap, "2026-06-29T12:00:00Z")
        self.assertEqual(bootstrap_origin, "bootstrap_lookback_days")
        self.assertEqual(resumed, "2026-07-01T00:00:00Z")
        self.assertEqual(resumed_origin, "state")


class IndustryHealthTests(unittest.TestCase):
    def industry_paths(self, root: Path):
        return (
            patch.object(discover_industry, "CONFIG", root / "sources-industry.json"),
            patch.object(discover_industry, "STATE", root / "state-industry.json"),
            patch.object(discover_industry, "FEED", root / "feed-industry.json"),
            patch.object(discover_industry, "REJECTED", root / "rejected-industry.json"),
            patch.object(discover_industry, "SLEEP", 0),
        )

    def write_config(self, root: Path) -> None:
        (root / "sources-industry.json").write_text(json.dumps({"health": {"default_failure_threshold": 1}, "sources": [{
            "id": "critical-rss", "name": "Critical RSS", "type": "rss", "url": "https://example.invalid/feed",
            "query_terms": [], "critical": True, "failure_threshold": 1,
        }]}), encoding="utf-8")

    def test_no_new_content_is_healthy_and_not_a_fetch_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_config(root)
            with ExitStack() as stack:
                for item in self.industry_paths(root):
                    stack.enter_context(item)
                stack.enter_context(patch.object(discover_industry, "fetch", return_value=EMPTY_RSS))
                stack.enter_context(patch.object(sys, "argv", ["discover_industry.py"]))
                self.assertEqual(discover_industry.main(), 0)
            feed = json.loads((root / "feed-industry.json").read_text(encoding="utf-8"))
            self.assertEqual(feed["count"], 0)
            self.assertEqual(feed["source_health"][0]["status"], "no_new_content")
            self.assertEqual(feed["source_health"][0]["consecutive_failures"], 0)

    def test_critical_fetch_error_is_structured_and_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_config(root)
            with ExitStack() as stack:
                for item in self.industry_paths(root):
                    stack.enter_context(item)
                stack.enter_context(patch.object(discover_industry, "fetch", side_effect=OSError("offline mock")))
                stack.enter_context(patch.object(sys, "argv", ["discover_industry.py"]))
                self.assertEqual(discover_industry.main(), 1)
            feed = json.loads((root / "feed-industry.json").read_text(encoding="utf-8"))
            self.assertEqual(feed["source_health"][0]["status"], "fetch_error")
            self.assertEqual(feed["items"], [])
            state = json.loads((root / "state-industry.json").read_text(encoding="utf-8"))
            self.assertEqual(state["health"]["critical-rss"]["consecutive_failures"], 1)

    def test_rebuild_with_source_error_preserves_existing_dedup_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_config(root)
            old_state = {
                "seen_titles": ["old title"],
                "seen_urls": ["https://example.invalid/old"],
            }
            (root / "state-industry.json").write_text(json.dumps(old_state), encoding="utf-8")
            with ExitStack() as stack:
                for item in self.industry_paths(root):
                    stack.enter_context(item)
                stack.enter_context(patch.object(discover_industry, "fetch", side_effect=OSError("offline mock")))
                stack.enter_context(patch.object(sys, "argv", ["discover_industry.py", "--rebuild"]))
                self.assertEqual(discover_industry.main(), 1)
            self.assertEqual(
                json.loads((root / "state-industry.json").read_text(encoding="utf-8")),
                old_state,
            )

    def test_critical_source_error_does_not_consume_other_source_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sources-industry.json").write_text(json.dumps({
                "health": {"default_failure_threshold": 1},
                "sources": [
                    {"id": "healthy", "name": "Healthy", "type": "rss", "url": "https://example.invalid/healthy", "query_terms": []},
                    {"id": "critical", "name": "Critical", "type": "rss", "url": "https://example.invalid/critical", "query_terms": [], "critical": True},
                ],
            }), encoding="utf-8")
            old_state = {"seen_titles": ["old title"], "seen_urls": ["https://example.invalid/old"]}
            (root / "state-industry.json").write_text(json.dumps(old_state), encoding="utf-8")
            rss = b"""<rss><channel><item><title>New perovskite module</title><link>https://example.invalid/new</link><description>new</description></item></channel></rss>"""

            def fetch_source(url):
                if url.endswith("/critical"):
                    raise OSError("offline mock")
                return rss

            with ExitStack() as stack:
                for item in self.industry_paths(root):
                    stack.enter_context(item)
                stack.enter_context(patch.object(discover_industry, "fetch", side_effect=fetch_source))
                stack.enter_context(patch.object(sys, "argv", ["discover_industry.py"]))
                self.assertEqual(discover_industry.main(), 1)

            state = json.loads((root / "state-industry.json").read_text(encoding="utf-8"))
            self.assertEqual(state["seen_titles"], old_state["seen_titles"])
            self.assertEqual(state["seen_urls"], old_state["seen_urls"])
            self.assertEqual(state["health"]["critical"]["consecutive_failures"], 1)


class PipelineFailFastTests(unittest.TestCase):
    def test_discovery_failure_never_invokes_renderers(self):
        with (
            patch.object(run_pipeline.discover_papers, "main", return_value=1),
            patch.object(run_pipeline.enrich_metadata, "main") as enrich,
            patch.object(run_pipeline.text_renderer, "main") as text,
            patch.object(run_pipeline.image_renderer, "main") as image,
            patch.object(sys, "argv", ["run_pipeline.py"]),
        ):
            self.assertEqual(run_pipeline.main(), 1)
        enrich.assert_not_called()
        text.assert_not_called()
        image.assert_not_called()


if __name__ == "__main__":
    unittest.main()
