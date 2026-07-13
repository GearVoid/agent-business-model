"""Offline presentation contracts for paired cards and compact links."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import image_renderer  # noqa: E402
from text_renderer import (  # noqa: E402
    render_compact_digest,
    with_delivery_indices,
)


def paper(index: int) -> dict:
    return {
        "id": f"paper-{index}",
        "title": f"Passivation and stability study {index} with a deliberately complete original title",
        "url": f"https://example.test/papers/{index}",
        "source_domain": "arxiv.org",
        "corresponding_source": "openalex",
        "published_date": f"2026-07-0{index}",
        "provenance_tier": "T1",
        "relevance_score": 0.95,
        "abstract": "ORIGINAL ABSTRACT MUST NOT APPEAR ON THE CARD",
    }


def industry(index: int) -> dict:
    return {
        "id": f"industry-{index}",
        "title": f"Perovskite module manufacturing update {index}",
        "url": f"https://example.test/industry/{index}",
        "source_name": "pv magazine",
        "published_date": f"2026-07-1{index}",
        "provenance_tier": "T3",
        "summary": "ORIGINAL INDUSTRY SUMMARY MUST NOT APPEAR ON THE CARD",
    }


class PresentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.papers = [paper(index) for index in range(1, 6)]
        self.industry = [industry(index) for index in range(1, 3)]

    def test_compact_is_deterministic_complete_link_index(self) -> None:
        first = render_compact_digest(self.papers, self.industry, "2026-07-13", 5, 2)
        second = render_compact_digest(self.papers, self.industry, "2026-07-13", 5, 2)

        self.assertEqual(first, second)
        for index in range(1, 8):
            self.assertIn(chr(0x245F + index), first)
        for item in [*self.papers, *self.industry]:
            self.assertIn(item["title"], first)
            self.assertIn(item["url"], first)

    def test_card_html_uses_the_compact_indices_without_internal_fields(self) -> None:
        indexed_papers, indexed_industry = with_delivery_indices(self.papers, self.industry)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(image_renderer, "OUTPUT_DIR", Path(tmp)):
                [card] = image_renderer.render_html(
                    indexed_papers,
                    "2026-07-13",
                    indexed_industry,
                )
            content = card.read_text(encoding="utf-8")

        for label in ("①", "②", "③", "⑥"):
            self.assertIn(label, content)
        for label in ("④", "⑤", "⑦"):
            self.assertNotIn(f">{label}<", content)
        for forbidden in (
            "http://",
            "https://",
            "score",
            "openalex",
            "original abstract",
            "original industry summary",
            "source verified",
        ):
            self.assertNotIn(forbidden, content.lower())
        self.assertIn("arXiv", content)
        self.assertIn("pv magazine", content)
        tag_lines = re.findall(r"<p class='tags'>(.*?)</p>", content)
        self.assertTrue(all(len(line.split()) <= 2 for line in tag_lines))

    def test_empty_feeds_still_render_text_and_card_shell(self) -> None:
        compact = render_compact_digest([], [], "2026-07-13", 0, 0)
        self.assertTrue(compact.strip())
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(image_renderer, "OUTPUT_DIR", Path(tmp)):
                [card] = image_renderer.render_html([], "2026-07-13", [])
            self.assertIn("<html", card.read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
