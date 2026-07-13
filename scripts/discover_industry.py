#!/usr/bin/env python3
"""Discover industry RSS feeds and publish explicit per-source health."""

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from relevance_filter import filter_industry_item  # noqa: E402
from text_utils import safe_reconfigure_stdout, sanitize_text  # noqa: E402
from tier_mapper import tier_for_url  # noqa: E402

CONFIG = BASE / "config" / "sources-industry.json"
FEED = BASE / "feed-industry.json"
REJECTED = BASE / "rejected-industry.json"
STATE = BASE / "state-industry.json"
SLEEP = 0.4
TIMEOUT = 25
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
ACCEPT = "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
DC = "{http://purl.org/dc/elements/1.1/}"
ATOM = "{http://www.w3.org/2005/Atom}"
SUBTIER_TYPES = {"curated-media", "official-newsroom"}
_TAG = re.compile(r"<[^>]+>")


def atomic_write_json(path: Path, data: object) -> None:
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


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": ACCEPT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return response.read()


def _strip_html(value: str) -> str:
    return sanitize_text(re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", value or ""))).strip())


def _txt(element, *tags) -> str:
    for tag in tags:
        value = element.findtext(tag)
        if value:
            return sanitize_text(value)
    return ""


def parse_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except Exception:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return ""


def extract_rss(item) -> dict:
    published = _txt(item, "pubDate")
    author = _txt(item, f"{DC}creator")
    return {"title": _txt(item, "title"), "url": _txt(item, "link"),
            "summary": _strip_html(item.findtext("description") or ""),
            "published_raw": published, "published_date": parse_date(published),
            "authors": [author] if author else []}


def extract_atom(entry) -> dict:
    link = next((link.get("href") for link in entry.findall(f"{ATOM}link") if link.get("href")), "")
    published = _txt(entry, f"{ATOM}published")
    authors = [sanitize_text(author.findtext(f"{ATOM}name") or "") for author in entry.findall(f"{ATOM}author")]
    return {"title": _txt(entry, f"{ATOM}title"), "url": sanitize_text(link),
            "summary": _strip_html(entry.findtext(f"{ATOM}summary") or ""),
            "published_raw": published, "published_date": parse_date(published),
            "authors": [author for author in authors if author]}


def parse_items(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = [extract_rss(item) for item in root.iter("item")]
    return items or [extract_atom(entry) for entry in root.iter(f"{ATOM}entry")]


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", (title or "").lower()).strip()


def make_id(url: str, title: str) -> str:
    return "ind:" + hashlib.sha1((url or title or "unknown").encode("utf-8")).hexdigest()[:12]


def health_record(source: dict, default_threshold: int) -> dict:
    source_id = source.get("id", "unknown")
    return {"source_id": source_id, "source_name": source.get("name", source_id),
            "critical": bool(source.get("critical", False)),
            "failure_threshold": int(source.get("failure_threshold", default_threshold)),
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "item_count": 0, "new_count": 0}


def main() -> int:
    safe_reconfigure_stdout()
    parser = argparse.ArgumentParser(description="perovskite-scout industry discovery")
    parser.add_argument("--rebuild", action="store_true", help="replace dedup state after this run")
    parser.add_argument("--ignore-state", action="store_true", help="do not deduplicate or write state")
    args = parser.parse_args()
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[FAIL] cannot read {CONFIG.name}: {exc}")
        return 1

    state: dict = {}
    if not args.rebuild and not args.ignore_state and STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("state root is not an object")
        except Exception as exc:
            print(f"[FAIL] cannot read {STATE.name}: {exc}")
            return 1
    seen_titles = set(state.get("seen_titles", []))
    seen_urls = set(state.get("seen_urls", []))
    previous_health = state.get("health", {}) if isinstance(state.get("health", {}), dict) else {}
    default_threshold = int(cfg.get("health", {}).get("default_failure_threshold", 1))

    kept, rejected, health = [], [], []
    now_titles, now_urls = set(), set()
    for source in (source for source in cfg.get("sources", []) if source.get("enabled", True)):
        record = health_record(source, default_threshold)
        if source.get("type", "rss") != "rss":
            record.update({"status": "unsupported_source_type", "error": f"unsupported type '{source.get('type')}'"})
            health.append(record)
            continue
        try:
            data = fetch(source["url"])
        except Exception as exc:  # source failures are never reclassified as content rejection
            record.update({"status": "fetch_error", "error": f"{type(exc).__name__}: {exc}"})
            health.append(record)
            continue
        time.sleep(SLEEP)
        try:
            items = parse_items(data)
        except Exception as exc:
            record.update({"status": "parse_error", "error": f"{type(exc).__name__}: {exc}"})
            health.append(record)
            continue

        record["item_count"] = len(items)
        terms = source.get("query_terms", [])
        subtier = source.get("source_type") if source.get("source_type") in SUBTIER_TYPES else None
        for item in items:
            if not item["url"] and not item["title"]:
                continue
            judged = filter_industry_item({**item, "summary": item["summary"][:600]}, terms)
            item_id, title_key = make_id(item["url"], item["title"]), norm_title(item["title"])
            duplicate = title_key in seen_titles or title_key in now_titles or (item["url"] and (item["url"] in seen_urls or item["url"] in now_urls))
            if not judged["keep"] or duplicate:
                rejected.append({"id": item_id, "source_id": source.get("id"), "source_name": source.get("name"),
                                 "title": item["title"], "url": item["url"],
                                 "reject_reason": "duplicate" if duplicate else judged["reject_reason"]})
                continue
            kept.append({"id": item_id, "title": item["title"], "url": item["url"], "summary": judged["summary"],
                         "source_id": source.get("id"), "source_name": source.get("name", source.get("id")),
                         "source_type": source.get("source_type"), "source_domain": urlparse(item["url"]).netloc,
                         "provenance_tier": tier_for_url(item["url"]), "provenance_subtier": subtier, "doi": None,
                         "type": "industry", "category": None, "published_date": item["published_date"],
                         "published_raw": item["published_raw"], "authors": item["authors"],
                         "relevance_score": judged["relevance_score"], "relevance_reason": judged["relevance_reason"],
                         "reject_reason": judged["reject_reason"], "keep": judged["keep"], "enriched": False})
            record["new_count"] += 1
            now_titles.add(title_key)
            if item["url"]:
                now_urls.add(item["url"])
        record["status"] = "ok" if items else "no_new_content"
        health.append(record)

    failures, next_health = [], {}
    failed_statuses = {"fetch_error", "parse_error", "unsupported_source_type"}
    for record in health:
        old = previous_health.get(record["source_id"], {})
        failed = record["status"] in failed_statuses
        record["consecutive_failures"] = int(old.get("consecutive_failures", 0)) + 1 if failed else 0
        record["last_failure_at" if failed else "last_success_at"] = record["checked_at"]
        next_health[record["source_id"]] = record
        if record["critical"] and failed and record["consecutive_failures"] >= record["failure_threshold"]:
            failures.append(record)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    feed = {"generated_at": now, "source": "industry", "count": len(kept), "items": kept, "source_health": health}
    rejected_feed = {"generated_at": now, "count": len(rejected), "items": rejected, "source_health": health}
    # Publishing state and feed uses atomic replacements; individual source errors remain visible in both artifacts.
    try:
        atomic_write_json(FEED, feed)
        atomic_write_json(REJECTED, rejected_feed)
        if not args.ignore_state:
            state["seen_titles"] = list(seen_titles | now_titles)
            state["seen_urls"] = list(seen_urls | now_urls)
            state["health"] = next_health
            atomic_write_json(STATE, state)
    except Exception as exc:
        print(f"[FAIL] cannot publish industry outputs: {exc}")
        return 1

    if failures:
        print("[FAIL] critical source health threshold reached: " + ", ".join(record["source_id"] for record in failures))
        return 1
    print(f"[OK] kept={len(kept)} rejected={len(rejected)} -> {FEED.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
