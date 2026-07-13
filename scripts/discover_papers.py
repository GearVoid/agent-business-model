"""Discover arXiv papers with a resumable, watermark-based scan.

The arXiv API is sorted newest-first.  A single Top-N request can therefore
permanently hide papers published during a busy week.  This module pages back
to the last *successful* scan watermark (with a configurable overlap), and
only advances that watermark after the entire scan has completed.
"""

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relevance_filter import filter_item  # noqa: E402
from text_utils import safe_reconfigure_stdout, sanitize_text  # noqa: E402
from tier_mapper import tier_for_url  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE / "config" / "sources.json"
FEED_PATH = BASE / "feed-papers.json"
REJECTED_PATH = BASE / "rejected-papers.json"
STATE_PATH = BASE / "state-feed.json"

ATOM_NS = "{http://www.w3.org/2005/Atom}"
USER_AGENT = "perovskite-scout/0.2 (watermark discovery; local run)"


def atomic_write_json(path: Path, data: object) -> None:
    """Replace a JSON file atomically, never leaving a partial JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def load_sources() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("state-feed.json must contain an object")
    return value


def state_watermark(state: dict) -> str | None:
    """Read v0.2 metadata while treating pre-v0.2 id maps as valid state."""
    return (
        state.get("_meta", {})
        .get("sources", {})
        .get("arxiv", {})
        .get("watermark")
    )


def seen_ids(state: dict) -> set[str]:
    """Both legacy {id: date} and the v0.2 state use top-level arxiv ids."""
    return {key for key in state if key.startswith("arxiv:")}


def parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_arxiv_page(cfg: dict, start: int, page_size: int) -> str:
    params = {
        "search_query": cfg["search_query"],
        "start": start,
        "max_results": page_size,
        "sortBy": cfg.get("sortBy", "submittedDate"),
        "sortOrder": cfg.get("sortOrder", "descending"),
    }
    url = cfg["base_url"] + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 503) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise last  # pragma: no cover - loop either returns or raises


def fetch_arxiv(cfg: dict) -> str:
    """Backward-compatible single-page helper for external callers."""
    return fetch_arxiv_page(cfg, int(cfg.get("start", 0)), int(cfg.get("page_size", 100)))


def parse_entries(xml_text: str, doc_type: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        raw_id = (entry.findtext(f"{ATOM_NS}id") or "").strip()
        title = sanitize_text(entry.findtext(f"{ATOM_NS}title"))
        summary = sanitize_text(entry.findtext(f"{ATOM_NS}summary"))
        published_at = (entry.findtext(f"{ATOM_NS}published") or "").strip()
        authors = [
            name for author in entry.findall(f"{ATOM_NS}author")
            if (name := sanitize_text(author.findtext(f"{ATOM_NS}name")))
        ]
        aid = raw_id.rsplit("/", 1)[-1] if raw_id else ""
        aid_no_ver = aid.rsplit("v", 1)[0] if "v" in aid else aid
        url = f"https://arxiv.org/abs/{aid_no_ver}"
        out.append({
            "id": f"arxiv:{aid_no_ver}", "title": title, "url": url,
            "source_domain": "arxiv.org", "provenance_tier": tier_for_url(url),
            "type": doc_type, "category": None, "published_date": published_at[:10],
            "published_at": published_at, "abstract": summary, "authors": authors,
            "raw_metrics": None,
        })
    return out


def scan_arxiv(cfg: dict, doc_type: str, watermark: str | None) -> tuple[list[dict], str | None, dict]:
    page_size = int(cfg.get("page_size", cfg.get("max_results", 100)))
    max_pages = int(cfg.get("max_pages", 20))
    overlap_days = int(cfg.get("watermark_overlap_days", 7))
    if page_size < 1 or max_pages < 1 or overlap_days < 0:
        raise ValueError("arXiv page_size/max_pages must be positive and watermark_overlap_days non-negative")

    previous = parse_timestamp(watermark or "")
    cutoff = previous - timedelta(days=overlap_days) if previous else None
    entries: list[dict] = []
    start = int(cfg.get("start", 0))

    for page_number in range(max_pages):
        page = parse_entries(fetch_arxiv_page(cfg, start + page_number * page_size, page_size), doc_type)
        entries.extend(page)
        page_times = [parse_timestamp(item.get("published_at", "")) for item in page]
        reached_watermark = bool(cutoff and any(ts and ts <= cutoff for ts in page_times))
        exhausted = len(page) < page_size
        if reached_watermark or exhausted:
            newest = max((ts for item in entries if (ts := parse_timestamp(item.get("published_at", "")))), default=None)
            return entries, (timestamp_text(newest) if newest else watermark), {
                "pages": page_number + 1,
                "entry_count": len(entries),
                "completed_by": "watermark" if reached_watermark else "exhausted",
                "previous_watermark": watermark,
            }

    raise RuntimeError(
        f"arXiv scan incomplete: reached max_pages={max_pages} before covering "
        f"watermark={watermark!r}; increase max_pages or reduce page_size"
    )


def main() -> int:
    safe_reconfigure_stdout()
    parser = argparse.ArgumentParser(description="perovskite-scout arXiv discovery")
    parser.add_argument("--ignore-state", action="store_true", help="do not deduplicate or write state")
    parser.add_argument("--rebuild", action="store_true", help="replace dedup state only after a successful scan")
    args = parser.parse_args()

    try:
        arxiv_cfg = load_sources().get("arxiv")
        if not arxiv_cfg or not arxiv_cfg.get("enabled", True):
            print("arXiv source not enabled; nothing to do.")
            return 0
        use_state = not args.ignore_state
        state = {} if args.rebuild else (load_state() if use_state else {})
        watermark = state_watermark(state) if use_state else None
        doc_type = arxiv_cfg.get("type", "paper")
        print(f"Scanning arXiv pages: size={arxiv_cfg.get('page_size', arxiv_cfg.get('max_results', 100))}, watermark={watermark or 'none'}")
        entries, next_watermark, scan = scan_arxiv(arxiv_cfg, doc_type, watermark)
    except Exception as exc:  # no feed/state write on an incomplete discovery scan
        print(f"[FAIL] arXiv discovery: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    filtered = [filter_item(entry) for entry in entries]
    kept = [entry for entry in filtered if entry["keep"]]
    rejected = [entry for entry in filtered if not entry["keep"]]
    old_seen = seen_ids(state)
    new_kept = [entry for entry in kept if entry["id"] not in old_seen] if use_state else kept
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    feed = {"generated_at": now, "source": "arxiv", "count": len(new_kept), "items": new_kept, "scan": scan}
    rejected_feed = {"generated_at": now, "source": "arxiv", "count": len(rejected), "items": rejected, "scan": scan}

    # Discovery completed above; only now is it safe to make its output visible.
    atomic_write_json(FEED_PATH, feed)
    atomic_write_json(REJECTED_PATH, rejected_feed)
    if use_state:
        for entry in new_kept:
            state[entry["id"]] = time.strftime("%Y-%m-%d")
        state.setdefault("_meta", {}).setdefault("sources", {})["arxiv"] = {
            "watermark": next_watermark,
            "last_success_at": now,
            "last_scan": scan,
        }
        atomic_write_json(STATE_PATH, state)

    print(f"[OK] scanned={len(entries)} kept={len(new_kept)} rejected={len(rejected)} pages={scan['pages']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
