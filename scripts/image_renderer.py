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
from text_renderer import (  # noqa: E402
    CARD_INDUSTRY_TOP_N,
    CARD_PAPER_TOP_N,
    COMPACT_INDUSTRY_TOP_N,
    TOP_MIN_SCORE,
    TOP_N,
    delivery_label,
    sort_industry,
    source_label,
    topic_tags,
    with_delivery_indices,
)
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
HEIGHT = 2040
MARGIN_X = 82
INDUSTRY_TOP_N = 2  # 图片里产业动态克制: 最多 2 条, 否则破坏整体克制感
RENDER_SCALE = 2    # 整图超采样后缩回 1080px，统一改善文字、细线和 badge 边缘

PAPER = (247, 243, 235)
INK = (29, 33, 36)
MUTED = (105, 111, 108)
HAIRLINE = (188, 187, 177)
GREEN = (49, 95, 74)
BLUE = (70, 107, 128)
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
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJKsc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/source-han-serif/SourceHanSerifCN-Regular.otf",
        "/usr/share/fonts/opentype/source-han-serif/SourceHanSerifSC-Regular.otf",
        "/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ],
    "body": [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Regular.otf",
        "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "bold": [
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/source-han-sans/SourceHanSansCN-Bold.otf",
        "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Bold.otf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "serif": [
        "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/times.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "/System/Library/Fonts/Georgia.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ],
}

CJK_FONT_MARKERS = (
    "msyh",
    "simhei",
    "simsun",
    "pingfang",
    "heiti",
    "songti",
    "notoSansCJK".lower(),
    "notoSerifCJK".lower(),
    "sourcehan",
    "wqy",
)


def find_font_path(role: str) -> str | None:
    for path in FONT_PATHS.get(role, FONT_PATHS["body"]):
        if Path(path).exists():
            return path
    return None


SELECTED_FONT_PATHS = {role: find_font_path(role) for role in FONT_PATHS}


def font_path_has_cjk(path: str | None) -> bool:
    if not path:
        return False
    normalized = path.lower().replace("-", "")
    return any(marker in normalized for marker in CJK_FONT_MARKERS)


ROLE_HAS_CJK = {
    role: font_path_has_cjk(path) for role, path in SELECTED_FONT_PATHS.items()
}
# 固定中文 UI 同时用到 title/body/bold；任一角色缺字时都不能宣称完整中文安全。
CJK_IMAGE_TEXT = all(ROLE_HAS_CJK.get(role, False) for role in ("title", "body", "bold"))

IMAGE_TEXT_TRANSLATION = str.maketrans(
    {
        "₀": "0",
        "₁": "1",
        "₂": "2",
        "₃": "3",
        "₄": "4",
        "₅": "5",
        "₆": "6",
        "₇": "7",
        "₈": "8",
        "₉": "9",
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
        "μ": "micro",
        "µ": "micro",
        "‐": "-",   # hyphen
        "‑": "-",   # non-breaking hyphen (部分 CJK 字体会画成 tofu)
        "‒": "-",   # figure dash
        "–": "-",   # en dash
        "—": "--",  # em dash
        "−": "-",   # minus sign
        " ": " ",   # narrow no-break space
    }
)

NON_CJK_PUNCT_TRANSLATION = str.maketrans(
    {
        "：": ": ",
        "，": ", ",
        "。": ". ",
        "；": "; ",
        "！": "!",
        "？": "?",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "《": "<",
        "》": ">",
        "、": ", ",
        "｜": "|",
        "·": ".",
    }
)

CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def ui_text(chinese: str, english: str, role: str = "body") -> str:
    """Use English for a fixed label when that label's font lacks CJK glyphs."""
    return chinese if ROLE_HAS_CJK.get(role, False) else english


def image_text(text: str | None, role: str = "body") -> str:
    """Normalize dynamic text for broad font support in raster cards.

    The full Unicode source remains intact in message.txt. On a font-poor cloud
    image, unsupported Chinese runs become an explicit [CN] marker instead of
    silent square glyphs; installing Noto/Source Han restores the original text.
    """
    normalized = sanitize_text(text or "").translate(IMAGE_TEXT_TRANSLATION)
    if not ROLE_HAS_CJK.get(role, False):
        normalized = CJK_RUN_RE.sub("[CN]", normalized)
        normalized = normalized.translate(NON_CJK_PUNCT_TRANSLATION)
        normalized = " ".join(normalized.split())
    return normalized


def load_font(size: int, role: str = "body"):
    path = SELECTED_FONT_PATHS.get(role) or SELECTED_FONT_PATHS.get("body")
    if path:
        try:
            return ImageFont.truetype(path, size * RENDER_SCALE)
        except OSError:
            pass
    fallback = "DejaVuSans-Bold.ttf" if role == "bold" else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(fallback, size * RENDER_SCALE)
    except OSError:
        pass
    try:
        return ImageFont.load_default(size=size * RENDER_SCALE)
    except TypeError:
        return ImageFont.load_default()


def text_w(font, text: str) -> float:
    return font.getlength(text) / RENDER_SCALE


def scaled(value: float | int) -> int:
    return int(round(value * RENDER_SCALE))


def scaled_box(values) -> list[int]:
    return [scaled(value) for value in values]


class ScaledDraw:
    """ImageDraw façade accepting the existing 1080px logical coordinates."""

    def __init__(self, image: Image.Image):
        self.raw = ImageDraw.Draw(image)

    def text(self, xy, text, *args, **kwargs):
        return self.raw.text((scaled(xy[0]), scaled(xy[1])), text, *args, **kwargs)

    def line(self, points, *args, **kwargs):
        width = kwargs.pop("width", 1)
        logical_points = [(scaled(x), scaled(y)) for x, y in points]
        return self.raw.line(
            logical_points,
            *args,
            width=max(1, scaled(width)),
            **kwargs,
        )

    def ellipse(self, box, *args, **kwargs):
        width = kwargs.pop("width", 1)
        return self.raw.ellipse(
            scaled_box(box),
            *args,
            width=max(1, scaled(width)),
            **kwargs,
        )


def wrap_text(
    text: str,
    font,
    max_width: int,
    max_lines: int | None = None,
    role: str = "body",
) -> list[str]:
    text = image_text(text, role=role).replace("\n", " ").strip()
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


def fmt_authors(authors: list[str], role: str = "body") -> str:
    if not authors:
        return "Unknown authors"
    first = image_text(str(authors[0]), role=role)
    return f"{first} et al." if len(authors) > 1 else first


def short_summary(abstract: str, chars: int = 190, role: str = "body") -> str:
    text = image_text(abstract or "", role=role).replace("\n", " ").strip()
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
    return sort_industry(items)[:INDUSTRY_TOP_N]


def draw_industry(draw: ScaledDraw, items: list[dict], y: int) -> int:
    if not items:
        return y
    label_role = "title" if ROLE_HAS_CJK.get("title", False) else "serif"
    label_font = load_font(34, label_role)
    title_font = load_font(27, "bold")
    meta_font = load_font(20, "body")

    draw.text(
        (MARGIN_X, y),
        ui_text("产业动态", "Industry Signals", role="title"),
        font=label_font,
        fill=GREEN,
    )
    draw.line([(MARGIN_X, y + 48), (204, y + 48)], fill=AMBER, width=3)
    y += 80

    for it in items:
        src = image_text(it.get("source_name", ""), role="body")
        draw.text((MARGIN_X, y), f"\u00b7 {src}", font=meta_font, fill=BLUE)

        title = it.get("title", "(untitled)")
        tl = wrap_text(
            title,
            title_font,
            WIDTH - 2 * MARGIN_X,
            max_lines=2,
            role="bold",
        )
        ty = y + 34
        for line in tl:
            draw.text((MARGIN_X, ty), line, font=title_font, fill=INK)
            ty += 33

        date = it.get("published_date", "")
        tier = str(it.get("provenance_tier", "T?"))[:2]
        subtier = str(it.get("provenance_subtier") or "source verified").replace("-", " ")
        meta = f"{date}  |  {tier}  |  {subtier}"
        draw.text((MARGIN_X, ty + 5), meta, font=meta_font, fill=MUTED)
        y = ty + 49
    return y


def paste_smooth_rounded_rectangle(
    img: Image.Image,
    box: list[int],
    radius: int,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int] | None = None,
    width: int = 1,
) -> None:
    """Draw a rounded rectangle on the supersampled canvas."""
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return

    layer_draw = ImageDraw.Draw(img)
    layer_draw.rounded_rectangle(
        scaled_box([x0, y0, x1, y1]),
        radius=scaled(radius),
        fill=fill,
        outline=outline,
        width=max(1, scaled(width)),
    )


def add_paper_texture(img: Image.Image) -> None:
    pix = img.load()
    w, h = img.size
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r, g, b = pix[x, y]
            # 只给纯纸面加颗粒，避免在最终缩采样后重新污染文字和 badge 边缘。
            if max(abs(r - PAPER[0]), abs(g - PAPER[1]), abs(b - PAPER[2])) > 1:
                continue
            delta = ((x * 17 + y * 31) % 7) - 3
            pix[x, y] = (
                max(0, min(255, r + delta)),
                max(0, min(255, g + delta)),
                max(0, min(255, b + delta)),
            )


def draw_source_mark(draw: ScaledDraw, x: int, y: int, font) -> None:
    draw.ellipse([x, y, x + 46, y + 46], outline=GREEN, width=2)
    draw.line([(x + 23, y - 10), (x + 23, y + 8)], fill=GREEN, width=2)
    draw.line([(x + 23, y + 38), (x + 23, y + 56)], fill=GREEN, width=2)
    draw.line([(x - 10, y + 23), (x + 8, y + 23)], fill=GREEN, width=2)
    draw.line([(x + 38, y + 23), (x + 56, y + 23)], fill=GREEN, width=2)
    draw.line([(x + 13, y + 24), (x + 21, y + 32), (x + 34, y + 15)], fill=GREEN, width=3)
    draw.text((x + 76, y + 6), "source details", font=font, fill=GREEN)
    wave_x = x + 500
    draw.line([(wave_x, y + 24), (wave_x + 78, y + 24), (wave_x + 102, y + 2), (wave_x + 130, y + 46), (wave_x + 154, y + 24), (wave_x + 238, y + 24)], fill=GREEN, width=2)
    draw.ellipse([wave_x + 236, y + 21, wave_x + 242, y + 27], fill=GREEN)


def draw_header(draw: ScaledDraw, today: str) -> None:
    title_font = load_font(72, "title")
    label_font = load_font(26, "serif")
    sub_font = load_font(33, "serif")
    small_font = load_font(23, "body")

    draw.line([(52, 64), (760, 64)], fill=HAIRLINE, width=2)
    draw.line([(52, 48), (52, 80)], fill=HAIRLINE, width=2)
    draw.ellipse([42, 54, 62, 74], outline=HAIRLINE, width=2)
    draw.text((820, 48), "Research Digest", font=label_font, fill=GREEN)

    draw.text(
        (MARGIN_X, 150),
        ui_text("钙钛矿情报雷达", "Perovskite Scout", role="title"),
        font=title_font,
        fill=INK,
    )
    draw.line([(MARGIN_X, 246), (690, 246)], fill=INK, width=2)
    draw.ellipse([688, 242, 696, 250], fill=INK)

    draw.text((MARGIN_X, 300), "Research Cards", font=sub_font, fill=GREEN)
    draw.line([(MARGIN_X, 348), (176, 348)], fill=AMBER, width=4)
    draw.text((MARGIN_X, 372), f"{today}  |  numbered reading guide", font=small_font, fill=MUTED)

    # Keep the masthead intentionally sparse. The earlier decorative molecule
    # and solar-stack sketch looked too literal once real content was rendered.
    draw.line([(782, 112), (946, 112)], fill=(205, 199, 185), width=1)
    draw.text((782, 140), "PVSC", font=label_font, fill=GREEN)
    draw.text((782, 176), "research cards", font=small_font, fill=MUTED)


def draw_item(img: Image.Image, draw: ScaledDraw, item: dict, idx: int, y: int) -> int:
    num_font = load_font(42, "serif")
    title_font = load_font(30, "bold")
    meta_font = load_font(21, "body")
    summary_font = load_font(23, "body")
    tier_font = load_font(21, "bold")

    left_x = MARGIN_X
    line_x = left_x + 78
    content_x = left_x + 112
    row_w = WIDTH - content_x - MARGIN_X
    row_h = 182

    draw.text((left_x, y + 14), f"{idx:02d}", font=num_font, fill=GREEN)
    draw.line([(line_x, y + 8), (line_x, y + row_h - 16)], fill=HAIRLINE, width=1)
    draw.ellipse([line_x - 5, y + 80, line_x + 5, y + 90], fill=GREEN)

    tier = str(item.get("provenance_tier", "T?"))[:2]
    tier_color = TIER_COLORS.get(tier, GREY)
    pill = [content_x, y + 11, content_x + 62, y + 49]
    paste_smooth_rounded_rectangle(img, pill, 18, fill=tier_color)
    draw.text(
        ((pill[0] + pill[2]) / 2, (pill[1] + pill[3]) / 2 - 1),
        tier,
        font=tier_font,
        fill=(255, 255, 255),
        anchor="mm",
    )

    title_x = content_x + 82
    title = item.get("title", "(untitled)")
    title_lines = wrap_text(title, title_font, row_w - 82, max_lines=2, role="bold")
    ty = y + 6
    for line in title_lines:
        draw.text((title_x, ty), line, font=title_font, fill=INK)
        ty += 36

    source = image_text(item.get("corresponding_source") or "arXiv", role="body")
    meta = f"{item.get('published_date', '')}  |  {fmt_authors(item.get('authors', []), role='body')}  |  score {item.get('relevance_score', '')}  |  {source}"
    draw.text((title_x, y + 84), ellipsize(meta, meta_font, row_w - 82), font=meta_font, fill=MUTED)

    summary = short_summary(item.get("abstract", ""), role="body")
    summary_lines = wrap_text(summary, summary_font, row_w - 8, max_lines=2, role="body")
    sy = y + 118
    for line in summary_lines:
        draw.text((content_x, sy), line, font=summary_font, fill=MUTED)
        sy += 29

    draw.line([(MARGIN_X, y + row_h), (WIDTH - MARGIN_X, y + row_h)], fill=HAIRLINE, width=1)
    return y + row_h + 18


def draw_delivery_item(img: Image.Image, draw: ScaledDraw, item: dict, y: int) -> int:
    """Draw one skim card using only the delivery-index presentation fields."""
    num_font = load_font(38, "serif")
    title_font = load_font(29, "bold")
    meta_font = load_font(21, "body")
    tag_font = load_font(19, "body")
    tier_font = load_font(20, "bold")
    left_x = MARGIN_X
    line_x = left_x + 78
    content_x = left_x + 112
    row_w = WIDTH - content_x - MARGIN_X
    row_h = 158

    index = int(item.get("delivery_index", 0))
    draw.text((left_x, y + 13), delivery_label(index), font=num_font, fill=GREEN)
    draw.line([(line_x, y + 8), (line_x, y + row_h - 16)], fill=HAIRLINE, width=1)
    draw.ellipse([line_x - 5, y + 70, line_x + 5, y + 80], fill=GREEN)

    tier = str(item.get("provenance_tier", "T?"))[:2]
    tier_color = TIER_COLORS.get(tier, GREY)
    pill = [content_x, y + 9, content_x + 62, y + 47]
    paste_smooth_rounded_rectangle(img, pill, 18, fill=tier_color)
    draw.text(
        ((pill[0] + pill[2]) / 2, (pill[1] + pill[3]) / 2 - 1),
        tier,
        font=tier_font,
        fill=(255, 255, 255),
        anchor="mm",
    )

    title_x = content_x + 82
    title_lines = wrap_text(item.get("title", "(untitled)"), title_font, row_w - 82, max_lines=2, role="bold")
    ty = y + 5
    for line in title_lines:
        draw.text((title_x, ty), line, font=title_font, fill=INK)
        ty += 35

    meta = f"{image_text(source_label(item), role='body')}  |  {item.get('published_date', '')}"
    draw.text((title_x, y + 83), ellipsize(meta, meta_font, row_w - 82), font=meta_font, fill=MUTED)
    tags = topic_tags(item)
    if tags:
        draw.text((content_x, y + 119), "  ".join(f"#{tag}" for tag in tags), font=tag_font, fill=BLUE)
    draw.line([(MARGIN_X, y + row_h), (WIDTH - MARGIN_X, y + row_h)], fill=HAIRLINE, width=1)
    return y + row_h + 18


def render_pil(
    top: list[dict], today: str, industry_items: list[dict] | None = None
) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (WIDTH * RENDER_SCALE, HEIGHT * RENDER_SCALE), PAPER)
    draw = ScaledDraw(img)

    draw_header(draw, today)

    y = 448
    for item in top[:CARD_PAPER_TOP_N]:
        y = draw_delivery_item(img, draw, item, y)

    industry_items = industry_items if industry_items is not None else load_industry_top()[:CARD_INDUSTRY_TOP_N]
    if industry_items:
        label_font = load_font(34, "serif")
        draw.text((MARGIN_X, y), "Industry Signals", font=label_font, fill=GREEN)
        draw.line([(MARGIN_X, y + 48), (204, y + 48)], fill=AMBER, width=3)
        y += 80
        for item in industry_items[:CARD_INDUSTRY_TOP_N]:
            y = draw_delivery_item(img, draw, item, y)

    foot_font = load_font(23, "serif")
    draw_source_mark(draw, MARGIN_X, HEIGHT - 92, foot_font)

    note_font = load_font(20, "body")
    note = ui_text(
        "完整链接见配套微信短版  |  tier 与相关性均由规则管线判定",
        "Full original titles and clickable links: next message ①–⑦",
        role="body",
    )
    draw.text((MARGIN_X, HEIGHT - 34), note, font=note_font, fill=MUTED)

    resample = getattr(Image, "Resampling", Image).LANCZOS
    img = img.resize((WIDTH, HEIGHT), resample, reducing_gap=3.0)
    add_paper_texture(img)
    out = OUTPUT_DIR / "perovskite-scout-card.png"
    img.save(out, optimize=True)
    return [out]


def render_html(
    top: list[dict], today: str, industry_items: list[dict] | None = None
) -> list[Path]:
    cards = []
    for item in top[:CARD_PAPER_TOP_N]:
        tier = str(item.get("provenance_tier", "T?"))[:2]
        color = "#%02x%02x%02x" % TIER_COLORS.get(tier, GREY)
        tags = " ".join(f"#{tag}" for tag in topic_tags(item))
        cards.append(
            "<section class='item'>"
            f"<div class='num'>{html.escape(delivery_label(int(item.get('delivery_index', 0))))}</div>"
            f"<div class='body'><span class='tier' style='background:{color}'>{html.escape(tier)}</span>"
            f"<h2>{html.escape(image_text(item.get('title', '(untitled)'), role='bold'))}</h2>"
            f"<p class='meta'>{html.escape(image_text(source_label(item), role='body'))} | "
            f"{html.escape(item.get('published_date', ''))}</p>"
            f"<p class='tags'>{html.escape(tags)}</p></div>"
            "</section>"
        )
    ind_top = industry_items if industry_items is not None else load_industry_top()[:CARD_INDUSTRY_TOP_N]
    ind_cards = []
    for it in ind_top[:CARD_INDUSTRY_TOP_N]:
        tier = str(it.get("provenance_tier", "T?"))[:2]
        color = "#%02x%02x%02x" % TIER_COLORS.get(tier, GREY)
        tags = " ".join(f"#{tag}" for tag in topic_tags(it))
        ind_cards.append(
            "<section class='item ind'>"
            f"<div class='num'>{html.escape(delivery_label(int(it.get('delivery_index', 0))))}</div>"
            f"<div class='body'><span class='tier' style='background:{color}'>{html.escape(tier)}</span>"
            f"<h2>{html.escape(image_text(it.get('title', '(untitled)'), role='bold'))}</h2>"
            f"<p class='meta'>{html.escape(image_text(source_label(it), role='body'))} | "
            f"{html.escape(it.get('published_date', ''))}</p>"
            f"<p class='tags'>{html.escape(tags)}</p></div></section>"
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
        f"<div class='rule'><span class='digest'>Research Digest</span></div><h1>{ui_text('钙钛矿情报雷达', 'Perovskite Scout', role='title')}</h1>"
        f"<div class='under'></div><div class='top'>Research Cards / {html.escape(today)}</div>"
        + "".join(cards)
        + (f"<div class='industry-h'>{ui_text('产业动态', 'Industry Signals', role='title')}</div>" + "".join(ind_cards) if ind_cards else "")
        + "<div class='foot'>Source details shown | Full original titles and clickable links: next message ①–⑦</div></main></body></html>"
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "perovskite-scout-card.html"
    out.write_text(page, encoding="utf-8")
    return [out]


def clean_old_outputs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for pat in (
        "perovskite-scout-card.png",
        "perovskite-scout-card-part-*.png",
        "perovskite-scout-card.html",
    ):
        for old in OUTPUT_DIR.glob(pat):
            try:
                old.unlink()
            except OSError as exc:
                failures.append(f"{old}: {exc}")
    if failures:
        raise RuntimeError("旧卡片清理失败，拒绝混用新旧产物: " + "; ".join(failures))


def main() -> int:
    safe_reconfigure_stdout()
    if not FEED_PATH.exists():
        print(f"ERROR: {FEED_PATH} does not exist; run scripts/discover_papers.py first", file=sys.stderr)
        return 1

    feed = json.load(open(FEED_PATH, encoding="utf-8"))
    delivery_papers, delivery_industry = with_delivery_indices(
        sort_top(feed.get("items", [])), load_industry_top()
    )
    card_papers = delivery_papers[:CARD_PAPER_TOP_N]
    card_industry = delivery_industry[:CARD_INDUSTRY_TOP_N]
    today = time.strftime("%Y-%m-%d")

    clean_old_outputs()
    if PIL_OK:
        if not CJK_IMAGE_TEXT:
            print("WARNING: no CJK font found; card image uses English labels. Install Noto Sans CJK for Chinese image labels.")
        files = render_pil(card_papers, today, card_industry)
        print(f"Pillow OK, generated {len(files)} card image(s):")
    else:
        files = render_html(card_papers, today, card_industry)
        print("Pillow unavailable, generated HTML fallback:")
    for file in files:
        print(f"  {file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
