from __future__ import annotations

"""Deterministic renderer for the hyperlocal bird report.

Pure in the same sense as :mod:`birds_data`: given the same
:class:`BirdPage`, this module always produces the same image.  It
reads no clock, no network, and no random source.  Illustrations are
checked-in files under ``assets/birds/``; a missing illustration is
an error, never a generated substitute.  Fonts are the repo's
vendored Roboto Serif / Roboto Flex only, so the same checkout
renders the same glyphs on any host.

Layout rules applied here (see the project's layout-rules file):
1872x1404, 16-level grayscale, at most three gray lines in any one
block, headings directly above their bird's name, main text in clear
print, and every diagram line bottom-anchored to its label, starting
past the label's measured end, and ending on the named feature.

Trade-off to be aware of before merging: the repo has no serif
italic, so "I wonder" lines here use Roboto Serif Regular in gray
rather than the prototype's Liberation Serif Italic.  Adding a new
vendored italic font would be a separate, explicit decision.
"""

import json
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw

from trmnl_weekly_calendar.birds_data import (
    BirdPage,
    load_snapshot,
    select_page,
)
from trmnl_weekly_calendar.png_encode import (
    encode_png_grayscale_4bit,
    quantize_grayscale_4bit,
)
from trmnl_weekly_calendar.render import F, H, OUT, PROJECT_ROOT, W, text_wh

BLACK = 0
GRAY = 85
LIGHT_GRAY = 170
ASSET_DIR = PROJECT_ROOT / "assets" / "birds"
SAMPLE_SNAPSHOT = Path(__file__).resolve().parent / "birds_snapshot_sample.json"

# Feature point as a fraction of the pasted illustration box.
# These are checked-in constants, measured against the checked-in
# PR illustration itself (2026-10-27, all 18 species), not computed
# at runtime.  Each point lands on the single feature named in
# assets/birds/FEATURE-POINTS.md.  Note: the three original points
# were re-measured on the PR assets on 2026-10-27 and corrected —
# the kinglet eye is at (0.22, 0.26), not (0.15, 0.32), and the
# creeper bill tip is at the far left edge (0.01, 0.26), not
# (0.30, 0.20), which does not land on the bill in the PR asset.
FEATURE_FRACTION = {
    "house-finch": (0.39, 0.56),
    "ruby-crowned-kinglet": (0.22, 0.26),
    "brown-creeper": (0.01, 0.26),
    "black-capped-chickadee": (0.67, 0.15),
    "blue-jay": (0.41, 0.09),
    "northern-flicker": (0.52, 0.38),
    "spotted-towhee": (0.60, 0.27),
    "evening-grosbeak": (0.81, 0.23),
    "downy-woodpecker": (0.35, 0.50),
    "chipping-sparrow": (0.71, 0.10),
    "dark-eyed-junco": (0.38, 0.30),
    "yellow-rumped-warbler": (0.44, 0.58),
    "white-crowned-sparrow": (0.65, 0.15),
    "orange-crowned-warbler": (0.33, 0.29),
    "gray-catbird": (0.50, 0.29),
    "townsend-solitaire": (0.39, 0.18),
    "lincolns-sparrow": (0.74, 0.43),
    "western-tanager": (0.37, 0.44),
}


def _quantize(image: Image.Image) -> Image.Image:
    return image.convert("L").point(lambda p: round(p / 17) * 17)


def _paste_illustration(
    img: Image.Image, slug: str, box: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    if not slug:
        raise FileNotFoundError("bird page entry has no illustration slug")
    path = ASSET_DIR / f"{slug}.png"
    if not path.exists():
        raise FileNotFoundError(f"checked-in bird illustration missing: {path}")
    art = _quantize(Image.open(path))
    x0, y0, x1, y1 = box
    art.thumbnail((x1 - x0, y1 - y0))
    px = x0 + (x1 - x0 - art.width) // 2
    py = y0 + (y1 - y0 - art.height) // 2
    img.paste(art, (px, py))
    return px, py, px + art.width, py + art.height


def _draw_mark(
    draw: ImageDraw.ImageDraw,
    label: str,
    label_xy: tuple[int, int],
    feature_xy: tuple[int, int],
    *,
    side: str = "right",
) -> None:
    if not label:
        return
    lx, ly = label_xy
    draw.text((lx, ly), label, font=F["event_small"], fill=GRAY)
    tw, th = text_wh(draw, label, F["event_small"])
    bottom = ly + th
    if side == "right":
        start = (lx + tw + 20, bottom)
    else:
        start = (lx - 20, bottom)
    draw.line([start, feature_xy], fill=GRAY, width=2)


def _feature_xy(box: tuple[int, int, int, int], slug: str) -> tuple[int, int]:
    fx, fy = FEATURE_FRACTION.get(slug, (0.5, 0.5))
    x0, y0, x1, y1 = box
    return (round(x0 + fx * (x1 - x0)), round(y0 + fy * (y1 - y0)))


def _gray_block(
    draw: ImageDraw.ImageDraw, lines: list[str], x: int, y: int, max_lines: int = 3
) -> int:
    for line in lines[:max_lines]:
        draw.text((x, y), line, font=F["event_small"], fill=GRAY)
        _, th = text_wh(draw, line, F["event_small"])
        y += th + 6
    return y


def _fit_also_line(
    draw: ImageDraw.ImageDraw, others: list, max_width: int
) -> str:
    """Build the "Also:" line, dropping the last species until it fits.

    Never let a word run off the right edge: measure the line with the
    actual font, and if it is wider than the box, drop the last species
    and measure again.  A top-5 list may therefore render as top-4, top-3,
    and so on, depending on species-name lengths and the font in use.
    If even one species alone would not fit, return "" so the caller
    omits the line instead of truncating a word.
    """
    names = [s.species for s in others]
    while names:
        line = f"Also: {', '.join(names)}"
        tw, _ = text_wh(draw, line, F["event_small"])
        if tw <= max_width:
            return line
        names.pop()
    return ""


def render_bird_image(page: BirdPage) -> Image.Image:
    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)
    month_name = page.page_date.strftime("%B")
    date_label = page.page_date.strftime("%B %d, %Y")

    draw.text((60, 30), "Neighborhood Birds", font=F["title"], fill=BLACK)
    draw.text(
        (60, 115),
        f"Golden, Colorado  •  {date_label}",
        font=F["meta"],
        fill=GRAY,
    )
    draw.line((60, 155, W - 60, 155), fill=GRAY, width=2)

    # Spotted here, large on the left.
    spotted_box = (60, 180, 820, 650)
    if page.spotted is not None:
        box = _paste_illustration(img, page.spotted.illustration, spotted_box)
        _draw_mark(
            draw,
            page.spotted.mark_label,
            (840, 300),
            _feature_xy(box, page.spotted.illustration),
            side="left",
        )
        draw.text((60, 670), page.spotted_label, font=F["day"], fill=BLACK)
        draw.text((60, 730), page.spotted.species, font=F["title"], fill=BLACK)
        _gray_block(
            draw,
            [
                f"{page.spotted.obs_date.strftime('%B %d, %Y')}  •  "
                f"{page.spotted.place}, {page.spotted.distance_mi:.2f} mi away",
                "Not yet on your life list",
                f"Habitat: {page.spotted.habitat}",
            ],
            60,
            830,
        )
        draw.text(
            (60, 1020),
            "I wonder: will it still be here tomorrow?",
            font=F["date"],
            fill=GRAY,
        )

    # Most common, upper right.
    monthly_box = (1080, 180, 1800, 500)
    box = _paste_illustration(img, page.monthly.illustration, monthly_box)
    _draw_mark(
        draw,
        page.monthly.mark_label,
        (840, 220),
        _feature_xy(box, page.monthly.illustration),
        side="right",
    )
    draw.text((1080, 520), f"Who's usually around in {month_name}?", font=F["day"], fill=BLACK)
    draw.text((1080, 580), page.monthly.species, font=F["title"], fill=BLACK)
    also_line = _fit_also_line(draw, page.also_monthly, monthly_box[2] - 1080)
    monthly_lines = [
        f"Most common  •  {page.monthly.days} {month_name} days, 2021-2025",
    ]
    if also_line:
        monthly_lines.append(also_line)
    monthly_lines.append(f"Habitat: {page.monthly.habitat}")
    _gray_block(
        draw,
        monthly_lines,
        1080,
        680,
    )

    # Back in town, lower right, or the absence note.
    if page.back is not None:
        back_box = (1080, 800, 1800, 1120)
        box = _paste_illustration(img, page.back.illustration, back_box)
        _draw_mark(
            draw,
            page.back.mark_label,
            (840, 900),
            _feature_xy(box, page.back.illustration),
            side="right",
        )
        draw.text((1080, 1140), "Back in town", font=F["day"], fill=BLACK)
        draw.text((1080, 1200), page.back.species, font=F["title"], fill=BLACK)
        _gray_block(
            draw,
            [
                f"{page.back.report_date.strftime('%b %d, %Y')}  •  "
                f"{page.back.hotspot}  •  Season: {page.back.season}",
                f"Habitat: {page.back.habitat}",
            ],
            1080,
            1300,
        )
    elif page.absence_note:
        draw.text((1080, 1140), page.absence_note, font=F["event_small"], fill=GRAY)

    return _quantize(img)


def render_bird_image_from_snapshot(
    snapshot_path: str | Path, page_date: date
) -> Image.Image:
    snapshot = load_snapshot(snapshot_path)
    return render_bird_image(select_page(snapshot, page_date))


def write_outputs(img: Image.Image) -> Path:
    """Write only the mockup the terminal actually uses.

    TRMNL serves a 4-bit grayscale PNG by default, so the mockup is
    that file alone.  The plain-grayscale and 1-bit dithered previews
    are not produced here: they are not what the terminal displays,
    and keeping them made the outputs folder look like three examples.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    gray4_path = OUT / "trmnl_birds_mockup_4bit_grayscale.png"
    gray4_path.write_bytes(encode_png_grayscale_4bit(quantize_grayscale_4bit(img)))
    return gray4_path


def render() -> None:
    """CLI entry point: render the checked-in sample snapshot only.

    The sample date is fixed (2026-10-26) on purpose: a mockup command
    must not change its output just because it is run on a different
    day.  Live pages use the server path with an explicit snapshot.
    """
    snapshot = json.loads(SAMPLE_SNAPSHOT.read_text(encoding="utf-8"))
    page = select_page(snapshot, date(2026, 10, 26))
    print(write_outputs(render_bird_image(page)))


if __name__ == "__main__":
    render()
