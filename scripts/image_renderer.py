"""Render the perovskite scout Top 5 card.

Input: feed-papers.json
Output: output/perovskite-scout-card.png when Pillow is available.
Fallback: output/perovskite-scout-card.html when Pillow is unavailable.

The visual direction follows the "academic editorial / research digest" mockup:
warm paper background, restrained typography, thin rules, small crystalline
accents, and source-verification cues. No LLM is used.
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from text_renderer import TOP_MIN_SCORE, TOP_N  # noqa: E402
from text_utils import sanitize_text, safe_reconfigure_stdout  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont

    PIL_OK = True
except ImportError:
    PIL_OK = False


BASE = Path(__file__).resolve().parent.parent
FEED_PATH = BASE / "feed-papers.json"
FEED_INDUSTRY_PATH = BASE / "feed-industry.json"
OUTPUT_DIR = BASE / "output"

WIDTH = 1080
HEIGHT = 1960
MARGIN_X = 82
INDUSTRY_TOP_N = 2  # 图片里产业动态克制: 最多 2 条, 否则破坏整体克制感

PAPER = (247, 243, 235)
INK = (29, 33, 36)
MUTED = (105, 111, 108)
HAIRLINE = (188, 187, 177)
GREEN = (49, 95, 74)
BLUE = (92, 129, 150)
AMBER = (213, 151, 42)
GREY = (128, 132, 128)

TIER_COLORS = {
    "T1": GREEN,
    "T2": BLUE,
    "T3": AMBER,
    "T4": GREY,
}

FONT_PATHS = {
    "title": [
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ],
    "body": [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ],
    "bold": [
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyh.ttc",
    ],
    "serif": [
        "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/times.ttf",
        "C:/Windows/Fonts/msyh.ttc",
    ],
}


def load_font(size: int, role: str = "body"):
    for path in FONT_PATHS.get(role, FONT_PATHS["body"]):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def text_w(font, text: str) -> float:
    return font.getlength(text)


def wrap_text(text: str, font, max_width: int, max_lines: int | None = None) -> list[str]:
    text = sanitize_text(text or "").replace("\n", " ").strip()
    if not text:
        return [""]

    tokens = re.findall(r"[\u4e00-\u9fff]|[^\u4e00-\u9fff\s]+|\s+", text)
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        trial = cur + tok
        if text_w(font, trial.strip()) <= max_width:
            cur = trial
            continue
        if cur.strip():
            lines.append(cur.strip())
        cur = tok if tok.strip() else ""
        if max_lines and len(lines) >= max_lines:
            break
    if cur.strip() and (not max_lines or len(lines) < max_lines):
        lines.append(cur.strip())

    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
    if max_lines and lines and text_w(font, lines[-1]) > max_width:
        lines[-1] = ellipsize(lines[-1], font, max_width)
    elif max_lines and len(lines) == max_lines:
        joined = "".join(lines)
        if len(joined) < len(text.replace(" ", "")):
            lines[-1] = ellipsize(lines[-1], font, max_width)
    return lines or [""]


def ellipsize(text: str, font, max_width: int) -> str:
    suffix = "..."
    while text and text_w(font, text + suffix) > max_width:
        text = text[:-1]
    return (text.rstrip() + suffix) if text else suffix


def fmt_authors(authors: list[str]) -> str:
    if not authors:
        return "Unknown authors"
    first = sanitize_text(str(authors[0]))
    return f"{first} et al." if len(authors) > 1 else first


def short_summary(abstract: str, chars: int = 150) -> str:
    text = sanitize_text(abstract or "").replace("\n", " ").strip()
    return text[:chars].rstrip() + "..." if len(text) > chars else text


def sort_top(items: list[dict]) -> list[dict]:
    items_sorted = sorted(
        items,
        key=lambda x: (x.get("relevance_score", 0), x.get("published_date", "")),
        reverse=True,
    )
    qualified = [it for it in items_sorted if (it.get("relevance_score") or 0) >= TOP_MIN_SCORE]
    top = qualified[:TOP_N]
    if len(top) < TOP_N:
        used = {id(it) for it in top}
        for it in items_sorted:
            if id(it) not in used:
                top.append(it)
                if len(top) >= TOP_N:
                    break
    return top


def load_industry_top() -> list[dict]:
    """读取 feed-industry.json, 取 curated-media 优先 + 最新的最多 2 条。"""
    if not FEED_INDUSTRY_PATH.exists():
        return []
    try:
        data = json.loads(FEED_INDUSTRY_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    items = data.get("items", [])
    rank = {"curated-media": 0, "official-newsroom": 1}
    items = sorted(items, key=lambda it: it.get("published_date", ""), reverse=True)
    items = sorted(items, key=lambda it: rank.get(it.get("provenance_subtier") or "", 2))
    return items[:INDUSTRY_TOP_N]


def draw_industry(draw: ImageDraw.ImageDraw, items: list[dict], y: int) -> int:
    if not items:
        return y
    label_font = load_font(33, "serif")
    title_font = load_font(24, "bold")
    meta_font = load_font(19, "body")

    draw.text((MARGIN_X, y), "产业动态", font=label_font, fill=GREEN)
    draw.line([(MARGIN_X, y + 46), (200, y + 46)], fill=AMBER, width=4)
    y += 78

    for it in items:
        src = it.get("source_name", "")
        draw.text((MARGIN_X, y), f"\u00b7 {src}", font=meta_font, fill=BLUE)

        title = sanitize_text(it.get("title", "(untitled)"))
        tl = wrap_text(title, title_font, WIDTH - 2 * MARGIN_X, max_lines=2)
        ty = y + 32
        for line in tl:
            draw.text((MARGIN_X, ty), line, font=title_font, fill=INK)
            ty += 30

        date = it.get("published_date", "")
        meta = ellipsize(f"{date}  |  {it.get('url', '')}", meta_font, WIDTH - 2 * MARGIN_X)
        draw.text((MARGIN_X, ty + 4), meta, font=meta_font, fill=MUTED)
        y = ty + 44
    return y


def rounded_rectangle(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def add_paper_texture(img: Image.Image) -> None:
    pix = img.load()
    w, h = img.size
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            delta = ((x * 17 + y * 31) % 7) - 3
            r, g, b = pix[x, y]
            pix[x, y] = (
                max(0, min(255, r + delta)),
                max(0, min(255, g + delta)),
                max(0, min(255, b + delta)),
            )


def draw_source_mark(draw: ImageDraw.ImageDraw, x: int, y: int, font) -> None:
    draw.ellipse([x, y, x + 46, y + 46], outline=GREEN, width=2)
    draw.line([(x + 23, y - 10), (x + 23, y + 8)], fill=GREEN, width=2)
    draw.line([(x + 23, y + 38), (x + 23, y + 56)], fill=GREEN, width=2)
    draw.line([(x - 10, y + 23), (x + 8, y + 23)], fill=GREEN, width=2)
    draw.line([(x + 38, y + 23), (x + 56, y + 23)], fill=GREEN, width=2)
    draw.line([(x + 13, y + 24), (x + 21, y + 32), (x + 34, y + 15)], fill=GREEN, width=3)
    draw.text((x + 76, y + 6), "source verified", font=font, fill=GREEN)
    wave_x = x + 500
    draw.line([(wave_x, y + 24), (wave_x + 78, y + 24), (wave_x + 102, y + 2), (wave_x + 130, y + 46), (wave_x + 154, y + 24), (wave_x + 238, y + 24)], fill=GREEN, width=2)
    draw.ellipse([wave_x + 236, y + 21, wave_x + 242, y + 27], fill=GREEN)


def draw_header(draw: ImageDraw.ImageDraw, today: str) -> None:
    title_font = load_font(72, "title")
    label_font = load_font(26, "serif")
    sub_font = load_font(33, "serif")
    small_font = load_font(23, "body")

    draw.line([(52, 64), (760, 64)], fill=HAIRLINE, width=2)
    draw.line([(52, 48), (52, 80)], fill=HAIRLINE, width=2)
    draw.ellipse([42, 54, 62, 74], outline=HAIRLINE, width=2)
    draw.text((820, 48), "Research Digest", font=label_font, fill=GREEN)

    draw.text((MARGIN_X, 150), "钙钛矿情报雷达", font=title_font, fill=INK)
    draw.line([(MARGIN_X, 246), (690, 246)], fill=INK, width=2)
    draw.ellipse([688, 242, 696, 250], fill=INK)

    draw.text((MARGIN_X, 300), "Top 5", font=sub_font, fill=GREEN)
    draw.line([(MARGIN_X, 348), (176, 348)], fill=AMBER, width=4)
    draw.text((MARGIN_X, 372), f"{today}  |  score >= {TOP_MIN_SCORE} prioritized", font=small_font, fill=MUTED)

    # Keep the masthead intentionally sparse. The earlier decorative molecule
    # and solar-stack sketch looked too literal once real content was rendered.
    draw.line([(782, 112), (946, 112)], fill=(205, 199, 185), width=1)
    draw.text((782, 140), "PVSC", font=label_font, fill=GREEN)
    draw.text((782, 176), "verified papers", font=small_font, fill=MUTED)


def draw_item(draw: ImageDraw.ImageDraw, item: dict, idx: int, y: int) -> int:
    num_font = load_font(42, "serif")
    title_font = load_font(28, "bold")
    meta_font = load_font(20, "body")
    summary_font = load_font(21, "body")
    tier_font = load_font(22, "bold")

    left_x = MARGIN_X
    line_x = left_x + 78
    content_x = left_x + 112
    row_w = WIDTH - content_x - MARGIN_X
    row_h = 166

    draw.text((left_x, y + 14), f"{idx:02d}", font=num_font, fill=GREEN)
    draw.line([(line_x, y + 8), (line_x, y + row_h - 16)], fill=HAIRLINE, width=2)
    draw.ellipse([line_x - 5, y + 80, line_x + 5, y + 90], fill=GREEN)

    tier = str(item.get("provenance_tier", "T?"))[:2]
    tier_color = TIER_COLORS.get(tier, GREY)
    pill = [content_x, y + 12, content_x + 58, y + 48]
    rounded_rectangle(draw, pill, 18, fill=tier_color)
    draw.text(
        (pill[0] + (58 - text_w(tier_font, tier)) / 2, pill[1] + 5),
        tier,
        font=tier_font,
        fill=(255, 255, 255),
    )

    title_x = content_x + 82
    title = sanitize_text(item.get("title", "(untitled)"))
    title_lines = wrap_text(title, title_font, row_w - 82, max_lines=2)
    ty = y + 8
    for line in title_lines:
        draw.text((title_x, ty), line, font=title_font, fill=INK)
        ty += 34

    source = item.get("corresponding_source") or "arXiv"
    meta = f"{item.get('published_date', '')}  |  {fmt_authors(item.get('authors', []))}  |  score {item.get('relevance_score', '')}  |  {source}"
    draw.text((title_x, y + 82), ellipsize(meta, meta_font, row_w - 82), font=meta_font, fill=MUTED)

    summary = short_summary(item.get("abstract", ""))
    summary_lines = wrap_text(summary, summary_font, row_w - 20, max_lines=1)
    sy = y + 114
    for line in summary_lines:
        draw.text((content_x, sy), line, font=summary_font, fill=(119, 119, 112))
        sy += 28

    draw.line([(MARGIN_X, y + row_h), (WIDTH - MARGIN_X, y + row_h)], fill=HAIRLINE, width=2)
    return y + row_h + 28


def render_pil(top: list[dict], today: str) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    add_paper_texture(img)
    draw = ImageDraw.Draw(img)

    draw_header(draw, today)

    y = 470
    for idx, item in enumerate(top, 1):
        y = draw_item(draw, item, idx, y)

    y = draw_industry(draw, load_industry_top(), y)

    foot_font = load_font(23, "serif")
    draw_source_mark(draw, MARGIN_X, HEIGHT - 92, foot_font)

    note_font = load_font(20, "body")
    note = "完整链接见配套 digest.txt  |  tier 与相关性均由规则管线判定"
    draw.text((MARGIN_X, HEIGHT - 34), note, font=note_font, fill=MUTED)

    out = OUTPUT_DIR / "perovskite-scout-card.png"
    img.save(out)
    return [out]


def render_html(top: list[dict], today: str) -> list[Path]:
    cards = []
    for idx, item in enumerate(top, 1):
        tier = str(item.get("provenance_tier", "T?"))[:2]
        color = "#%02x%02x%02x" % TIER_COLORS.get(tier, GREY)
        cards.append(
            "<section class='item'>"
            f"<div class='num'>{idx:02d}</div>"
            f"<div class='body'><span class='tier' style='background:{color}'>{html.escape(tier)}</span>"
            f"<h2>{html.escape(sanitize_text(item.get('title', '(untitled)')))}</h2>"
            f"<p class='meta'>{html.escape(item.get('published_date', ''))} | "
            f"{html.escape(fmt_authors(item.get('authors', [])))} | "
            f"score {html.escape(str(item.get('relevance_score', '')))}</p>"
            f"<p>{html.escape(short_summary(item.get('abstract', '')))}</p></div>"
            "</section>"
        )
    ind_top = load_industry_top()
    ind_cards = []
    for it in ind_top:
        ind_cards.append(
            "<section class='item ind'>"
            f"<div class='src'>{html.escape(it.get('source_name', ''))}</div>"
            f"<h3>{html.escape(sanitize_text(it.get('title', '(untitled)')))}</h3>"
            f"<p class='meta'>{html.escape(it.get('published_date', ''))} | "
            f"{html.escape(it.get('url', ''))}</p></section>"
        )
    page = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>"
        "body{margin:0;background:#f7f3eb;color:#1d2124;font-family:Georgia,'Microsoft YaHei',serif;}"
        ".wrap{max-width:920px;margin:0 auto;padding:56px 72px 44px;}"
        ".rule{border-top:1px solid #aaa;margin-bottom:54px}.digest{float:right;color:#315f4a}"
        "h1{font-size:64px;margin:0 0 20px}.under{height:2px;background:#1d2124;width:580px;margin-bottom:48px}"
        ".top{color:#315f4a;font-size:32px;margin-bottom:34px}"
        ".item{display:grid;grid-template-columns:80px 1fr;gap:28px;border-bottom:1px solid #bbb;padding:24px 0}"
        ".num{font-size:42px;color:#315f4a}.tier{color:#fff;border-radius:18px;padding:4px 13px;font-weight:700}"
        "h2{font:700 25px 'Microsoft YaHei',sans-serif;margin:12px 0 8px}.meta,p{font:18px/1.55 'Microsoft YaHei',sans-serif;color:#666}"
        ".ind{border-bottom:1px solid #ddd;background:#fbf8f1;padding:18px 24px}"
        ".ind .src{color:#5c8196;font-weight:700;margin-bottom:6px}.ind h3{font:700 21px 'Microsoft YaHei',sans-serif;margin:0 0 6px}"
        ".industry-h{color:#315f4a;font-size:30px;margin:40px 0 10px}"
        ".foot{margin-top:32px;color:#315f4a}"
        "</style></head><body><main class='wrap'>"
        f"<div class='rule'><span class='digest'>Research Digest</span></div><h1>钙钛矿情报雷达</h1>"
        f"<div class='under'></div><div class='top'>Top 5 / {html.escape(today)}</div>"
        + "".join(cards)
        + ("<div class='industry-h'>产业动态</div>" + "".join(ind_cards) if ind_cards else "")
        + "<div class='foot'>source verified | 完整链接见配套 digest.txt</div></main></body></html>"
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "perovskite-scout-card.html"
    out.write_text(page, encoding="utf-8")
    return [out]


def clean_old_outputs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for pat in (
        "perovskite-scout-card.png",
        "perovskite-scout-card-part-*.png",
        "perovskite-scout-card.html",
    ):
        for old in OUTPUT_DIR.glob(pat):
            try:
                old.unlink()
            except OSError:
                pass


def main() -> int:
    safe_reconfigure_stdout()
    if not FEED_PATH.exists():
        print(f"ERROR: {FEED_PATH} does not exist; run scripts/discover_papers.py first", file=sys.stderr)
        return 1

    feed = json.load(open(FEED_PATH, encoding="utf-8"))
    top = sort_top(feed.get("items", []))
    today = time.strftime("%Y-%m-%d")

    clean_old_outputs()
    if PIL_OK:
        files = render_pil(top, today)
        print(f"Pillow OK, generated {len(files)} card image(s):")
    else:
        files = render_html(top, today)
        print("Pillow unavailable, generated HTML fallback:")
    for file in files:
        print(f"  {file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
