"""validate_outputs.py — MVP 产出完整性 + Top5 一致性校验。

校验:
  - feed-papers.json: 存在/合法/非空; 每条含必需字段; tier 由 tier_mapper 机器判定; score∈[0,1]
  - rejected-papers.json: 存在; 每条含 reject_reason
  - digest.txt: 存在; Top5 与 feed 计算一致; 无 PZT/探测器禁词
  - card: png 或 html 存在; PNG 时校验宽 1080

退出码: 0=全绿, 1=有失败项。
"""

import json
import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from text_renderer import TOP_N, TOP_MIN_SCORE  # 复用排序逻辑
from text_utils import safe_reconfigure_stdout  # noqa: E402
from enrich_metadata import load_openalex_mailto  # noqa: E402
from feed_dedup import build_signatures, is_dup_of_papers  # noqa: E402

ENRICH_CONFIG = BASE / "config" / "enrich.json"

FEED = BASE / "feed-papers.json"
REJECTED = BASE / "rejected-papers.json"
FEED_INDUSTRY = BASE / "feed-industry.json"
REJECTED_INDUSTRY = BASE / "rejected-industry.json"
OUTPUT = BASE / "output"

REQUIRED = [
    "id", "title", "url", "source_domain", "provenance_tier", "type",
    "published_date", "abstract", "authors", "relevance_score",
    "relevance_reason", "keep",
]
FORBID = ["PZT", "PFN", "charged particle", "radiation detector"]

results = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    results.append((name, cond))
    print(f"[{'OK' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def sort_top(items: list) -> list:
    s = sorted(
        items,
        key=lambda x: (x.get("relevance_score", 0), x.get("published_date", "")),
        reverse=True,
    )
    q = [it for it in s if (it.get("relevance_score") or 0) >= TOP_MIN_SCORE]
    top = q[:TOP_N]
    if len(top) < TOP_N:
        ids = {id(it) for it in top}
        for it in s:
            if id(it) not in ids:
                top.append(it)
                if len(top) >= TOP_N:
                    break
    return top


def main() -> int:
    safe_reconfigure_stdout()  # Windows GBK 终端下避免打印中文/特殊符号时崩溃
    # 定时投递场景下, 某周 arXiv/行业可能确实无新命中 (feed 合法为空)。
    # 设 ALLOW_EMPTY_FEED=1 时, 把两个「非空」硬检查降级为通过, 避免安静周卡住投递;
    # 其它所有检查 (字段/乱码/tier/跨 feed 去重/卡片) 仍严格。默认 (未设) 保持开发态严格。
    # 必须在 main() 内读取, 因为 deliver.py 在 import 之后才设置该环境变量。
    ALLOW_EMPTY_FEED = os.environ.get("ALLOW_EMPTY_FEED") == "1"
    feed_items = []
    top = []

    # ---- feed ----
    if not FEED.exists():
        check("feed-papers.json 存在", False)
    else:
        try:
            feed = json.load(open(FEED, encoding="utf-8"))
            feed_items = feed.get("items", [])
            if ALLOW_EMPTY_FEED and not feed_items:
                check("feed 合法 JSON 且非空", True, "允许为空(定时模式)")
            else:
                check("feed 合法 JSON 且非空", bool(feed_items), f"count={feed.get('count')}")

            missing = [it.get("id") for it in feed_items if not all(k in it for k in REQUIRED)]
            check("feed 每条含必需字段", not missing, f"缺字段: {missing[:3]}")

            from tier_mapper import tier_for_url

            tier_bad = [
                it.get("id")
                for it in feed_items
                if it.get("provenance_tier") != tier_for_url(it.get("url", ""))
            ]
            check("tier 由 tier_mapper 机器判定", not tier_bad, f"不符: {tier_bad[:3]}")

            score_bad = [
                it.get("id")
                for it in feed_items
                if not isinstance(it.get("relevance_score"), (int, float))
                or not (0 <= it["relevance_score"] <= 1)
            ]
            check("relevance_score ∈ [0,1]", not score_bad, f"异常: {score_bad[:3]}")

            # ---- enrich 字段存在性 (允许 null, 但不允许缺失) ----
            ENRICH_KEYS = ["doi", "openalex_id", "institutions",
                           "corresponding_source", "enrich_errors"]
            miss_e = [it.get("id") for it in feed_items
                      if not all(k in it for k in ENRICH_KEYS)]
            check("feed 每条含 enrich 字段(允许null)", not miss_e, f"缺: {miss_e[:3]}")

            bad_inst = [it.get("id") for it in feed_items
                        if not isinstance(it.get("institutions"), list)]
            check("institutions 为列表", not bad_inst, f"异常: {bad_inst[:3]}")

            bad_src = [it.get("id") for it in feed_items
                       if it.get("corresponding_source") not in (None, "openalex", "crossref")]
            check("corresponding_source 取值合法", not bad_src, f"异常: {bad_src[:3]}")

            # ---- 乱码检查: feed 内不得含 Unicode 替换字符 U+FFFD ----
            REPL = "\ufffd"
            garble = [
                it.get("id")
                for it in feed_items
                if REPL in (it.get("title", "") or "") or REPL in (it.get("abstract", "") or "")
            ]
            check("feed 无乱码(U+FFFD)", not garble, f"命中: {garble[:3]}")

            top = sort_top(feed_items)
        except Exception as e:  # noqa: BLE001
            check("feed 解析", False, str(e))

    # ---- rejected ----
    if REJECTED.exists():
        rj = json.load(open(REJECTED, encoding="utf-8"))
        ritems = rj.get("items", [])
        no_reason = [it.get("id") for it in ritems if not it.get("reject_reason")]
        check("rejected 每条含 reject_reason", not no_reason, f"缺: {no_reason[:3]}")
    else:
        check("rejected-papers.json 存在", False)

    # ---- feed-industry ----
    industry_items = []
    if FEED_INDUSTRY.exists():
        try:
            ifeed = json.load(open(FEED_INDUSTRY, encoding="utf-8"))
            industry_items = ifeed.get("items", [])
            if ALLOW_EMPTY_FEED and not industry_items:
                check("feed-industry 合法 JSON 且非空", True, "允许为空(定时模式)")
            else:
                check("feed-industry 合法 JSON 且非空", bool(industry_items),
                      f"count={ifeed.get('count')}")

            IREQ = ["id", "title", "url", "source_id", "source_name", "source_type",
                    "source_domain", "provenance_tier", "provenance_subtier",
                    "type", "published_date", "summary", "authors", "keep", "doi"]
            imiss = [it.get("id") for it in industry_items if not all(k in it for k in IREQ)]
            check("feed-industry 每条含必需字段", not imiss, f"缺: {imiss[:3]}")

            from tier_mapper import tier_for_url
            itier_bad = [
                it.get("id")
                for it in industry_items
                if it.get("provenance_tier") != tier_for_url(it.get("url", ""))
            ]
            check("industry tier 由 tier_mapper 机器判定", not itier_bad, f"不符: {itier_bad[:3]}")

            isub_bad = [
                it.get("id")
                for it in industry_items
                if it.get("provenance_subtier") not in (None, "curated-media", "official-newsroom")
            ]
            check("industry provenance_subtier 取值合法", not isub_bad, f"异常: {isub_bad[:3]}")

            idoi_bad = [
                it.get("id")
                for it in industry_items
                if not isinstance(it.get("doi"), (str, type(None)))
            ]
            check("industry doi 字段合法(str/None)", not idoi_bad, f"异常: {idoi_bad[:3]}")

            igarble = [
                it.get("id")
                for it in industry_items
                if "\ufffd" in (it.get("title", "") or "") or "\ufffd" in (it.get("summary", "") or "")
            ]
            check("feed-industry 无乱码(U+FFFD)", not igarble, f"命中: {igarble[:3]}")
        except Exception as e:  # noqa: BLE001
            check("feed-industry 解析", False, str(e))
    else:
        check("feed-industry.json 存在", False)

    # ---- 跨 feed 去重: industry 不得与 papers 重复 (归一标题 / URL / DOI) ----
    if industry_items and feed_items:
        st, su, sd = build_signatures(feed_items)
        dups = []
        for it in industry_items:
            r = is_dup_of_papers(it, st, su, sd)
            if r:
                dups.append((it.get("id"), r))
        check("跨 feed 无重复 (标题/URL/DOI)", not dups, f"重复: {dups[:3]}")

    # ---- rejected-industry ----
    if REJECTED_INDUSTRY.exists():
        rij = json.load(open(REJECTED_INDUSTRY, encoding="utf-8"))
        ritems = rij.get("items", [])
        no_reason = [it.get("id") or it.get("source_id") for it in ritems if not it.get("reject_reason")]
        check("rejected-industry 每条含 reject_reason", not no_reason, f"缺: {no_reason[:3]}")
    else:
        check("rejected-industry.json 存在", False)

    # ---- digest ----
    digest = OUTPUT / "perovskite-scout-digest.txt"
    if digest.exists():
        t = digest.read_text(encoding="utf-8")
        dtop = re.findall(r"\[T[1-4]\] (.+)", t)[:TOP_N]
        expected = [it["title"] for it in top]
        check("digest Top5 与 feed 一致", dtop == expected,
              f"digest={len(dtop)} feed={len(expected)}")
        hit = [k for k in FORBID if k.lower() in t.lower()]
        check("digest 无噪声禁词", not hit, f"命中: {hit}")
        check("digest 无乱码(U+FFFD)", "\ufffd" not in t)
        if industry_items:
            check("digest 含产业动态区", "产业动态" in t)
            if "产业动态" in t:
                seg = t.split("产业动态", 1)[1]
                bullets = [l for l in seg.splitlines() if l.strip().startswith("- ")]
                check("digest 产业动态条数 ≤ 5", len(bullets) <= 5, f"条数={len(bullets)}")
    else:
        check("digest.txt 存在", False)

    # ---- card ----
    png = OUTPUT / "perovskite-scout-card.png"
    parts = sorted(OUTPUT.glob("perovskite-scout-card-part-*.png"))
    html = OUTPUT / "perovskite-scout-card.html"
    card_ok = png.exists() or bool(parts) or html.exists()
    check("card 产物存在 (png/html)", card_ok)
    if png.exists():
        try:
            from PIL import Image

            im = Image.open(png)
            check("card PNG 宽 1080", im.size[0] == 1080, f"size={im.size}")
        except Exception:  # noqa: BLE001
            pass
    if html.exists():
        check("card html 无乱码(U+FFFD)", "\ufffd" not in html.read_text(encoding="utf-8"))

    # ---- OpenAlex mailto 配置 ----
    configured = os.environ.get("OPENALEX_MAILTO") or (
        ENRICH_CONFIG.exists()
        and bool((json.load(open(ENRICH_CONFIG, encoding="utf-8")) or {}).get("openalex_mailto"))
    )
    check(
        "OpenAlex mailto 已配置 (env 或 config/enrich.json)",
        bool(configured),
        "设置 OPENALEX_MAILTO 环境变量或在 config/enrich.json 填 openalex_mailto",
    )

    failed = [n for n, c in results if not c]
    print("\n" + ("[OK] 全部校验通过" if not failed else f"[FAIL] {len(failed)} 项失败"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
