"""text_renderer.py — 纯文本简报渲染 (MVP, 无 LLM / 无微信 / 无图片)。

输入: feed-papers.json (+ rejected-papers.json 用于 footer 的过滤计数)
输出: stdout (可直接复制到微信) + output/perovskite-scout-digest.txt
      正文超过 3500 字时自动分页: output/perovskite-scout-digest-part-N.txt

约束 (MVP 红线):
  - 不调用 LLM: 摘要用 abstract 前 180 字符截断
  - 不用 Markdown 表格 / HTML: 微信可直复制
"""

import json
import sys
import time
from pathlib import Path

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
PAGE_LIMIT = 3500    # 单页最大字符数, 超出自动分页


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
    industry_items: list[dict] = []
    if INDUSTRY_FEED_PATH.exists():
        try:
            ifeed = json.load(open(INDUSTRY_FEED_PATH, encoding="utf-8"))
            industry_items = sort_industry(ifeed.get("items", []))[:INDUSTRY_TOP_N]
        except Exception:
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
    for pat in ("perovskite-scout-digest.txt", "perovskite-scout-digest-part-*.txt"):
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
