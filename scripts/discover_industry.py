#!/usr/bin/env python3
"""Stage 1.6: industry portal / official newsroom discovery.

Reads config/sources-industry.json, fetches each enabled RSS source,
applies a light keyword gate, machine-judges provenance tier + subtier,
dedups against state-industry.json, and writes feed-industry.json
(+ rejected-industry.json).

No LLM is used. Tier is judged by URL domain (tier_mapper);
subtier (curated-media / official-newsroom) comes from the source config.
Prototype scope: only `type: rss` sources are handled; html-monitor /
api / monitored-asset are parsed by later stages.
"""
import argparse
import hashlib
import html
import json
import re
import sys
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
from text_utils import sanitize_text, safe_reconfigure_stdout  # noqa: E402
from tier_mapper import tier_for_url  # noqa: E402

CONFIG = BASE / "config" / "sources-industry.json"
FEED = BASE / "feed-industry.json"
REJECTED = BASE / "rejected-industry.json"
STATE = BASE / "state-industry.json"

SLEEP = 0.4  # 礼貌性限速: 源之间最小间隔(秒)
TIMEOUT = 25
# pv magazine 等会拦默认 UA, 必须发浏览器 UA 才能拿到 RSS
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
ACCEPT = "application/rss+xml, application/xml;q=0.9, */*;q=0.8"

DC = "{http://purl.org/dc/elements/1.1/}"
ATOM = "{http://www.w3.org/2005/Atom}"

# 这些 source_type 才在 item 上记 provenance_subtier (T3 里的"可信子级")
SUBTIER_TYPES = {"curated-media", "official-newsroom"}

_TAG = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------- #
# 抓取 / 解析
# --------------------------------------------------------------------------- #
def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": ACCEPT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def parse_items(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = [extract_rss(it) for it in root.iter("item")]
    if items:
        return items
    return [extract_atom(en) for en in root.iter(f"{ATOM}entry")]


def _strip_html(s: str) -> str:
    s = _TAG.sub(" ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return sanitize_text(s)


def _txt(el, *tags) -> str:
    for t in tags:
        v = el.findtext(t)
        if v:
            return sanitize_text(v)
    return ""


def extract_rss(it) -> dict:
    return {
        "title": _txt(it, "title"),
        "url": _txt(it, "link"),
        "summary": _strip_html(it.findtext("description") or ""),
        "published_raw": _txt(it, "pubDate"),
        "published_date": parse_date(_txt(it, "pubDate")),
        "authors": [_txt(it, f"{DC}creator")] if _txt(it, f"{DC}creator") else [],
    }


def extract_atom(en) -> dict:
    link = ""
    for l in en.findall(f"{ATOM}link"):
        if l.get("href"):
            link = l.get("href")
            break
    authors = [sanitize_text(a.findtext(f"{ATOM}name") or "")
               for a in en.findall(f"{ATOM}author")]
    authors = [a for a in authors if a]
    return {
        "title": _txt(en, f"{ATOM}title"),
        "url": sanitize_text(link),
        "summary": _strip_html(en.findtext(f"{ATOM}summary") or ""),
        "published_raw": _txt(en, f"{ATOM}published"),
        "published_date": parse_date(_txt(en, f"{ATOM}published")),
        "authors": authors,
    }


def parse_date(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    try:
        dt = parsedate_to_datetime(s)
        if dt:
            return dt.date().isoformat()
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# 过滤 / 去重
# --------------------------------------------------------------------------- #
def passes_gate(item: dict, terms: list[str]) -> tuple[bool, str]:
    if not terms:
        return True, "source-curated (no gate)"
    hay = (item["title"] + " " + item["summary"]).lower()
    for t in terms:
        if t.lower() in hay:
            return True, f"keyword-match: {t}"
    return False, "no keyword match"


def norm_title(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", t)
    return t.strip()


def make_id(url: str, title: str) -> str:
    base = url or title or "unknown"
    return "ind:" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    safe_reconfigure_stdout()
    ap = argparse.ArgumentParser(description="perovskite-scout industry discovery")
    ap.add_argument("--rebuild", action="store_true",
                    help="清空 state-industry.json 后重新抓取(重置去重基线)")
    ap.add_argument("--ignore-state", action="store_true",
                    help="忽略去重(仍会更新 state), 产出本轮全部抓取")
    args = ap.parse_args()

    if not CONFIG.exists():
        print(f"[FAIL] 缺配置: {CONFIG}")
        return 1
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    sources = [s for s in cfg.get("sources", []) if s.get("enabled", True)]

    state: dict = {}
    if args.rebuild and STATE.exists():
        STATE.unlink()
    if not args.ignore_state and STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    seen_titles = set(state.get("seen_titles", []))
    seen_urls = set(state.get("seen_urls", []))

    kept: list[dict] = []
    rejected: list[dict] = []
    now_titles: set[str] = set()
    now_urls: set[str] = set()

    for src in sources:
        stype = src.get("type", "rss")
        if stype != "rss":
            rejected.append({
                "source_id": src.get("id"), "source_name": src.get("name"),
                "title": src.get("name", ""), "url": src.get("url", ""),
                "reject_reason": f"unsupported type '{stype}' (not in prototype)",
            })
            continue
        try:
            data = fetch(src["url"])
        except Exception as e:  # noqa: BLE001
            rejected.append({
                "source_id": src.get("id"), "source_name": src.get("name"),
                "title": src.get("name", ""), "url": src.get("url", ""),
                "reject_reason": f"fetch error: {e}",
            })
            continue
        time.sleep(SLEEP)
        try:
            items = parse_items(data)
        except Exception as e:  # noqa: BLE001
            rejected.append({
                "source_id": src.get("id"), "source_name": src.get("name"),
                "title": src.get("name", ""), "url": src.get("url", ""),
                "reject_reason": f"parse error: {e}",
            })
            continue

        terms = src.get("query_terms", [])
        subtier = src.get("source_type") if src.get("source_type") in SUBTIER_TYPES else None

        for it in items:
            if not it["url"] and not it["title"]:
                continue
            ok, reason = passes_gate(it, terms)
            tid = make_id(it["url"], it["title"])
            nt = norm_title(it["title"])
            dup = (nt in seen_titles or nt in now_titles or
                    (it["url"] and (it["url"] in seen_urls or it["url"] in now_urls)))
            if not ok:
                rejected.append({
                    "id": tid, "source_id": src.get("id"),
                    "source_name": src.get("name"), "title": it["title"],
                    "url": it["url"], "reject_reason": reason,
                })
                continue
            if dup:
                rejected.append({
                    "id": tid, "source_id": src.get("id"),
                    "source_name": src.get("name"), "title": it["title"],
                    "url": it["url"], "reject_reason": "duplicate",
                })
                continue

            tier = tier_for_url(it["url"])
            rec = {
                "id": tid,
                "title": it["title"],
                "url": it["url"],
                "summary": it["summary"][:600],
                "source_id": src.get("id"),
                "source_name": src.get("name", src.get("id")),
                "source_type": src.get("source_type"),
                "source_domain": urlparse(it["url"]).netloc,
                "provenance_tier": tier,
                "provenance_subtier": subtier,
                "doi": None,
                "type": "industry",
                "category": None,
                "published_date": it["published_date"],
                "published_raw": it["published_raw"],
                "authors": it["authors"],
                "relevance_score": 1.0,
                "relevance_reason": reason,
                "reject_reason": None,
                "keep": True,
                "enriched": False,
            }
            kept.append(rec)
            now_titles.add(nt)
            if it["url"]:
                now_urls.add(it["url"])

    # 更新去重记忆
    if not args.ignore_state:
        all_t = list(seen_titles) + [t for t in now_titles if t not in seen_titles]
        all_u = list(seen_urls) + [u for u in now_urls if u not in seen_urls]
        STATE.write_text(
            json.dumps({"seen_titles": all_t, "seen_urls": all_u},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    FEED.write_text(json.dumps({
        "generated_at": now,
        "source": "industry",
        "count": len(kept),
        "items": kept,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    REJECTED.write_text(json.dumps({
        "generated_at": now,
        "count": len(rejected),
        "items": rejected,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] kept={len(kept)} rejected={len(rejected)} -> {FEED.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
