"""text_renderer.py — 纯文本简报渲染 (MVP, 无 LLM / 无微信 / 无图片)。

输入: feed-papers.json (+ rejected-papers.json 用于 footer 的过滤计数)
输出: stdout (可直接复制到微信) + output/perovskite-scout-digest.txt
      正文超过 3500 字时自动分页: output/perovskite-scout-digest-part-N.txt
      微信链接伴侣短版: output/perovskite-scout-digest-compact.txt

约束 (MVP 红线):
  - 不调用 LLM: 摘要用 abstract 前 180 字符截断
  - 不用 Markdown 表格 / HTML: 微信可直复制
"""

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from text_utils import sanitize_text, safe_reconfigure_stdout

BASE = Path(__file__).resolve().parent.parent
FEED_PATH = BASE / "feed-papers.json"
REJECTED_PATH = BASE / "rejected-papers.json"
INDUSTRY_FEED_PATH = BASE / "feed-industry.json"
OUTPUT_DIR = BASE / "output"

TOP_N = 5            # 简报展示的重点条数 (可调)
TOP_MIN_SCORE = 0.7   # 重点条门槛: 仅 score >= 此值的进 Top; 不足则补余下最高分
SUMMARY_CHARS = 180  # 一句话摘要截断字符数
INDUSTRY_TOP_N = 5   # 产业动态区展示条数 (文本可多放, 图片只放 2)
COMPACT_INDUSTRY_TOP_N = 2  # 短版与图片产业区保持一致
COMPACT_TITLE_CHARS = 118   # 长英文标题在微信里最多占两到三行
COMPACT_LIMIT = 2800        # 为微信单条消息预留投递头部余量
PAGE_LIMIT = 3500    # 单页最大字符数, 超出自动分页


# Delivery is deliberately a presentation concern. These limits do not change
# the canonical feeds or the relevance/tier decisions that produced them.
COMPACT_PAPER_TOP_N = TOP_N
CARD_PAPER_TOP_N = 3
CARD_INDUSTRY_TOP_N = 1

# Ordered, title-only keyword rules keep card tags inspectable and stable. A
# tag is shown only when its explicit keyword rule matches; no model inference
# or rewriting is involved.
TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("passivation", ("passivation", "passivated")),
    ("stability", ("stability", "stable", "degradation", "durability")),
    ("interfaces", ("interface", "surface", "contact")),
    ("tandem", ("tandem", "multijunction")),
    ("wide-bandgap", ("wide bandgap", "wide-bandgap")),
    ("fabrication", ("fabrication", "processing", "printing", "coating")),
    ("modules", ("module", "modules", "scale-up", "scalable")),
    ("lead-free", ("lead-free", "lead free", "tin-based", "tin based")),
)

_CIRCLED_INDICES = ("", "\u2460", "\u2461", "\u2462", "\u2463", "\u2464", "\u2465", "\u2466", "\u2467", "\u2468", "\u2469")


def delivery_label(index: int) -> str:
    """Return the compact label shared by the card and link index."""
    return _CIRCLED_INDICES[index] if 0 < index < len(_CIRCLED_INDICES) else f"({index})"


def with_delivery_indices(
    papers: list[dict], industry_items: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Copy selected presentation items and assign one continuous index series."""
    indexed_papers = [dict(item, delivery_index=index) for index, item in enumerate(papers, 1)]
    first_industry_index = len(indexed_papers) + 1
    indexed_industry = [
        dict(item, delivery_index=index)
        for index, item in enumerate(industry_items, first_industry_index)
    ]
    return indexed_papers, indexed_industry


def source_label(item: dict) -> str:
    """Provide a reader-facing source name without exposing enrich metadata."""
    source = sanitize_text(item.get("source_name", "")).strip()
    if source:
        return source
    domain = str(item.get("source_domain") or urlparse(str(item.get("url", ""))).netloc)
    domain = domain.lower().removeprefix("www.")
    if domain == "arxiv.org":
        return "arXiv"
    return domain or "Source"


def topic_tags(item: dict, limit: int = 2) -> list[str]:
    """Return up to ``limit`` deterministic tags from the original title."""
    title = sanitize_text(item.get("title", "")).lower()
    return [
        label
        for label, keywords in TOPIC_KEYWORDS
        if any(keyword in title for keyword in keywords)
    ][:limit]


def fmt_authors(authors: list) -> str:
    if not authors:
        return "未知作者"
    head = authors[:3]
    s = ", ".join(head)
    if len(authors) > 3:
        s += " et al."
    return s


def fmt_summary(abstract: str) -> str:
    if not abstract:
        return "(无摘要)"
    text = abstract.replace("\n", " ").strip()
    if len(text) > SUMMARY_CHARS:
        text = text[:SUMMARY_CHARS].rstrip() + "…"
    return text


def render_item(it: dict, idx: int | None = None) -> str:
    tier = it.get("provenance_tier", "?")
    title = sanitize_text(it.get("title", "(无标题)"))
    date = it.get("published_date", "")
    authors = fmt_authors(it.get("authors", []))
    score = it.get("relevance_score", "")
    summary = fmt_summary(sanitize_text(it.get("abstract", "")))
    url = it.get("url", "")
    head = f"[{tier}] {title}"
    meta = f"{date}｜{authors}｜{score}"
    line3 = f"一句话摘要：{summary}"
    lines = [head, meta, line3, url]
    if idx is not None:
        lines.insert(0, f"{idx}.")
    return "\n".join(lines)


def sort_industry(items: list[dict]) -> list[dict]:
    """产业动态排序: curated-media / official-newsroom 优先, 其次按日期新到旧。

    用两次稳定排序: 先按日期降序, 再按 subtier 排名升序 (Python sort 稳定,
    同排名内保持日期顺序)。
    """
    rank = {"curated-media": 0, "official-newsroom": 1}
    items = sorted(items, key=lambda it: it.get("published_date", ""), reverse=True)
    items = sorted(items, key=lambda it: rank.get(it.get("provenance_subtier") or "", 2))
    return items


def render_industry_item(it: dict) -> str:
    src = it.get("source_name", "")
    title = sanitize_text(it.get("title", "(无标题)"))
    date = it.get("published_date", "")
    url = it.get("url", "")
    summary = ""
    if it.get("summary"):
        summary = "｜" + sanitize_text(it["summary"])[:80]
    return "\n".join([
        f"- {src}：{title}",
        f"  {date}{summary}",
        f"  {url}",
    ])


def compact_title(value: str | None) -> str:
    """为微信短版截断标题；URL 保持完整，确保仍可点击。"""
    title = sanitize_text(value or "(无标题)")
    return title


def render_compact_digest(
    top: list[dict],
    industry_items: list[dict],
    today: str,
    papers_count: int,
    industry_count: int,
) -> str:
    """渲染微信短版：摘要留在图片，文本专注于可点击的原始链接。

    top 与 industry_items 均由本模块既有确定性排序结果传入；这里不重新
    判定 tier、relevance 或入选资格，也不调用 LLM。
    """
    delivery_papers, industry_top = with_delivery_indices(
        top[:COMPACT_PAPER_TOP_N], industry_items[:COMPACT_INDUSTRY_TOP_N]
    )
    lines = [
        f"钙钛矿情报雷达｜{today}",
        f"论文 {papers_count} 条 · 产业 {industry_count} 条",
        "看图读摘要，文字点链接。",
    ]

    if delivery_papers:
        lines.extend(["", f"论文 Top {len(delivery_papers)}"])
        for it in delivery_papers:
            tier = it.get("provenance_tier", "?")
            lines.extend([
                f"{delivery_label(it['delivery_index'])} [{tier}] {compact_title(it.get('title'))}",
                str(it.get("url", "")),
            ])

    lines.extend(["", f"产业动态 Top {len(industry_top)}"])
    if industry_top:
        for it in industry_top:
            tier = it.get("provenance_tier", "?")
            source = source_label(it)
            lines.extend([
                f"{delivery_label(it['delivery_index'])} [{tier}] {source}｜{compact_title(it.get('title'))}",
                str(it.get("url", "")),
            ])
    else:
        lines.append("（本期无行业动态）")

    lines.extend(["", "完整摘要见长版文本；tier 与相关性均由规则管线判定。"])
    return "\n".join(lines).strip()


def main() -> int:
    safe_reconfigure_stdout()  # Windows GBK 终端下避免打印中文/特殊符号时崩溃
    if not FEED_PATH.exists():
        print(f"ERROR: {FEED_PATH} 不存在, 请先运行 discover_papers.py", file=sys.stderr)
        return 1

    feed = json.load(open(FEED_PATH, encoding="utf-8"))
    items = feed.get("items", [])

    # 排序: relevance_score 降序, 再 published_date 降序
    items_sorted = sorted(
        items,
        key=lambda x: (x.get("relevance_score", 0), x.get("published_date", "")),
        reverse=True,
    )
    # Top 优先取 score >= TOP_MIN_SCORE, 上限 TOP_N; 不足 TOP_N 再补余下最高分
    qualified = [it for it in items_sorted if (it.get("relevance_score") or 0) >= TOP_MIN_SCORE]
    capped = qualified[:TOP_N]
    top_ids = {id(it) for it in capped}
    top = list(capped)
    if len(top) < TOP_N:
        for it in items_sorted:
            if id(it) not in top_ids:
                top.append(it)
                if len(top) >= TOP_N:
                    break
    remaining = len(items_sorted) - len(top)

    today = time.strftime("%Y-%m-%d")
    filtered_count = 0
    if REJECTED_PATH.exists():
        rj = json.load(open(REJECTED_PATH, encoding="utf-8"))
        filtered_count = rj.get("count", len(rj.get("items", [])))
    new_count = feed.get("count", len(items))

    # 组装正文块
    blocks = [f"钙钛矿情报雷达 {today}\n本周重点 Top {len(top)} (评分≥{TOP_MIN_SCORE} 优先) / 共 {len(items_sorted)} 条"]
    for i, it in enumerate(top, 1):
        blocks.append(render_item(it, idx=i))
    if remaining > 0:
        blocks.append(f"… 其余 {remaining} 条见 feed-papers.json")

    # ---- 产业动态区 ----
    blocks.append(f"产业动态 (Top {INDUSTRY_TOP_N})")
    industry_all: list[dict] = []
    industry_items: list[dict] = []
    if INDUSTRY_FEED_PATH.exists():
        try:
            ifeed = json.load(open(INDUSTRY_FEED_PATH, encoding="utf-8"))
            industry_all = sort_industry(ifeed.get("items", []))
            industry_items = industry_all[:INDUSTRY_TOP_N]
        except Exception:
            industry_all = []
            industry_items = []
    if industry_items:
        for it in industry_items:
            blocks.append(render_industry_item(it))
    else:
        blocks.append("（本期无行业动态）")

    footer = (
        f"\n本次新发现 {new_count} 条；过滤 {filtered_count} 条；"
        f"完整列表见 feed-papers.json / feed-industry.json / rejected-*.json"
    )

    # 分页: 在块边界切分, 每页 <= PAGE_LIMIT
    pages: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for b in blocks:
        blen = len(b) + 2  # + 空行
        if cur and cur_len + blen > PAGE_LIMIT:
            pages.append("\n\n".join(cur))
            cur = []
            cur_len = 0
        cur.append(b)
        cur_len += blen
    if cur:
        pages.append("\n\n".join(cur))
    if pages:
        last = pages[-1]
        if not last.endswith(footer):
            pages[-1] = last + footer

    # 清理旧的分页产物, 避免投递错文件
    for pat in (
        "perovskite-scout-digest.txt",
        "perovskite-scout-digest-part-*.txt",
        "perovskite-scout-digest-compact.txt",
    ):
        for old in OUTPUT_DIR.glob(pat):
            try:
                old.unlink()
            except OSError:
                pass

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    page_files: list[Path] = []
    # 始终写一份完整 digest.txt (供复制 / 校验); 超出 PAGE_LIMIT 时再额外写分页件,
    # 用于微信分多条发送。两份内容一致, 只是分页件按块边界切过。
    full_text = "\n\n".join(pages)
    out = OUTPUT_DIR / "perovskite-scout-digest.txt"
    out.write_text(full_text, encoding="utf-8")
    page_files = [out]
    if len(pages) > 1:
        for i, p in enumerate(pages, 1):
            po = OUTPUT_DIR / f"perovskite-scout-digest-part-{i}.txt"
            po.write_text(p, encoding="utf-8")
            page_files.append(po)

    compact = render_compact_digest(
        top=top,
        industry_items=industry_all,
        today=today,
        papers_count=len(items_sorted),
        industry_count=len(industry_all),
    )
    compact_out = OUTPUT_DIR / "perovskite-scout-digest-compact.txt"
    compact_out.write_text(compact, encoding="utf-8")
    page_files.append(compact_out)

    # 控制台输出 (可直接复制)
    print("=" * 44)
    for p in pages:
        print(p)
        print("\n" + "-" * 44)
    print("输出文件:")
    for f in page_files:
        print(f"  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
