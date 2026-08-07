from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from trmnl_weekly_calendar.energy_data import (
    AveragePowerPoint,
    DailyEnergyValue,
    EnergyDashboardData,
    EnergyPlotSeries,
    mock_energy_dashboard,
)
from trmnl_weekly_calendar.png_encode import (
    encode_png_grayscale_4bit,
    quantize_grayscale_4bit,
)
from trmnl_weekly_calendar.render import FONT_DIR, H, OUT, W, ellipsize, font, text_wh


PANEL_COUNT = 16
GRID_COLUMNS = 4
GRID_ROWS = 4
DISPLAY_DAYS = 7

CANVAS_MARGIN = 38
HEADER_BOTTOM = 102
CONTENT_BOTTOM = 1348
GRID_RIGHT = 1372
SUMMARY_LEFT = 1410
SUMMARY_RIGHT = W - CANVAS_MARGIN
PANEL_GAP = 12
GRID_SUBTITLE = "15-MIN AVERAGE POWER • kW • DAILY VALUES: TODAY / YESTERDAY kWh"

WHITE = 255
BLACK = 0
TEXT_GRAY = 68
FRAME_GRAY = 105
YESTERDAY_GRAY = 170
COMPLETED_BAR_GRAY = 102
PARTIAL_BAR_GRAY = 221

ROBOTO_SERIF = FONT_DIR / "RobotoSerif.ttf"
ROBOTO_FLEX = FONT_DIR / "RobotoFlex.ttf"

FONTS = {
    "title": font(ROBOTO_SERIF, 42, "Bold"),
    "subtitle": font(ROBOTO_FLEX, 20, "Regular", browser_optical_size=True),
    "panel": font(ROBOTO_FLEX, 20, "SemiBold", browser_optical_size=True),
    "panel_value": font(ROBOTO_FLEX, 18, "Regular", browser_optical_size=True),
    "axis": font(ROBOTO_FLEX, 15, "Regular", browser_optical_size=True),
    "chart_title": font(ROBOTO_SERIF, 29, "Bold"),
    "chart_value": font(ROBOTO_FLEX, 17, "SemiBold", browser_optical_size=True),
    "chart_day": font(ROBOTO_FLEX, 16, "Regular", browser_optical_size=True),
    "note": font(ROBOTO_FLEX, 17, "Regular", browser_optical_size=True),
}


def render_energy_image(
    data: EnergyDashboardData | None = None,
    *,
    now: datetime | None = None,
) -> Image.Image:
    dashboard = data or mock_energy_dashboard(now)
    image = Image.new("L", (W, H), WHITE)
    draw = ImageDraw.Draw(image)

    draw_header(draw, dashboard)
    panels = stable_plot_series(dashboard.series)
    for index, panel in enumerate(panels):
        draw_energy_panel(draw, panel, panel_box(index), emphasize=index == 0)

    chart_gap = 24
    chart_top = 164
    chart_bottom = 1239
    chart_height = (chart_bottom - chart_top - chart_gap) // 2
    draw_daily_bars(
        draw,
        (SUMMARY_LEFT, chart_top, SUMMARY_RIGHT, chart_top + chart_height),
        "TOTAL ENERGY",
        dashboard.daily_mains,
        "kWh",
        dashboard.today,
    )
    draw_daily_bars(
        draw,
        (
            SUMMARY_LEFT,
            chart_top + chart_height + chart_gap,
            SUMMARY_RIGHT,
            chart_bottom,
        ),
        "TV SCREEN TIME",
        dashboard.daily_tv_hours,
        "hr",
        dashboard.today,
    )
    draw_footer(draw, dashboard)
    return image


def stable_plot_series(series: Sequence[EnergyPlotSeries]) -> tuple[EnergyPlotSeries, ...]:
    """Keep Total first and Balance last while preserving physical-channel order."""

    total = [item for item in series if item.key == "total"]
    balance = [item for item in series if item.key == "balance"]
    physical = [item for item in series if item.key not in {"total", "balance"}]
    ordered = tuple(total + physical + balance)
    if ordered and len(ordered) != PANEL_COUNT:
        raise ValueError(f"energy dashboard requires {PANEL_COUNT} plot series")
    return ordered


def panel_box(index: int) -> tuple[int, int, int, int]:
    if not 0 <= index < PANEL_COUNT:
        raise IndexError(index)
    usable_width = GRID_RIGHT - CANVAS_MARGIN
    usable_height = CONTENT_BOTTOM - HEADER_BOTTOM
    panel_width = (usable_width - PANEL_GAP * (GRID_COLUMNS - 1)) / GRID_COLUMNS
    panel_height = (usable_height - PANEL_GAP * (GRID_ROWS - 1)) / GRID_ROWS
    column = index % GRID_COLUMNS
    row = index // GRID_COLUMNS
    x0 = round(CANVAS_MARGIN + column * (panel_width + PANEL_GAP))
    y0 = round(HEADER_BOTTOM + row * (panel_height + PANEL_GAP))
    x1 = round(CANVAS_MARGIN + (column + 1) * panel_width + column * PANEL_GAP)
    y1 = round(HEADER_BOTTOM + (row + 1) * panel_height + row * PANEL_GAP)
    return x0, y0, x1, y1


def panel_plot_box(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return x0 + 35, y0 + 48, x1 - 12, y1 - 26


def draw_header(draw: ImageDraw.ImageDraw, data: EnergyDashboardData) -> None:
    draw.text((CANVAS_MARGIN, 27), "HOME ENERGY", font=FONTS["title"], fill=BLACK)
    draw.text(
        (CANVAS_MARGIN, 76),
        GRID_SUBTITLE,
        font=FONTS["subtitle"],
        fill=TEXT_GRAY,
    )
    draw.text(
        (SUMMARY_LEFT, 43),
        "7-DAY OVERVIEW",
        font=FONTS["subtitle"],
        fill=TEXT_GRAY,
    )


def draw_energy_panel(
    draw: ImageDraw.ImageDraw,
    series: EnergyPlotSeries,
    box: tuple[int, int, int, int],
    *,
    emphasize: bool,
) -> None:
    x0, y0, x1, y1 = box
    label, totals, totals_x = panel_header_layout(draw, series, box)
    draw.text((x0 + 10, y0 + 7), label, font=FONTS["panel"], fill=BLACK)
    draw.text((totals_x, y0 + 9), totals, font=FONTS["panel_value"], fill=TEXT_GRAY)

    plot = panel_plot_box(box)
    px0, py0, px1, py1 = plot
    maximum = panel_y_max(series)
    axis_width = 2 if emphasize else 1
    draw.line((px0, py0, px0, py1), fill=FRAME_GRAY, width=axis_width)
    draw.line((px0, py1, px1, py1), fill=FRAME_GRAY, width=axis_width)
    for hour in (0, 6, 12, 18, 24):
        x = round(px0 + hour / 24 * (px1 - px0))
        draw.line((x, py1, x, py1 + 4), fill=FRAME_GRAY, width=1)
        label_text = str(hour)
        label_width, _ = text_wh(draw, label_text, FONTS["axis"])
        draw.text((x - label_width / 2, py1 + 3), label_text, font=FONTS["axis"], fill=TEXT_GRAY)
    max_label = f"{compact_number(maximum)} kW"
    max_width, _ = text_wh(draw, max_label, FONTS["axis"])
    draw.text((px0 - max_width - 5, py0 - 8), max_label, font=FONTS["axis"], fill=TEXT_GRAY)
    draw.text((px0 - 8, py1 - 9), "0", font=FONTS["axis"], fill=TEXT_GRAY, anchor="ra")

    draw_series_line(draw, series.yesterday, plot, maximum, YESTERDAY_GRAY, 3, False)
    draw_series_line(draw, series.today, plot, maximum, BLACK, 5, True)


def panel_header_layout(
    draw: ImageDraw.ImageDraw,
    series: EnergyPlotSeries,
    box: tuple[int, int, int, int],
) -> tuple[str, str, int]:
    """Fit a circuit name beside explicit today/yesterday daily kWh totals."""

    x0, _, x1, _ = box
    totals = panel_totals_text(series)
    totals_width, _ = text_wh(draw, totals, FONTS["panel_value"])
    totals_x = x1 - totals_width - 10
    label_width = max(1, totals_x - (x0 + 10) - 8)
    label = ellipsize(draw, series.label, FONTS["panel"], label_width)
    return label, totals, round(totals_x)


def panel_totals_text(series: EnergyPlotSeries) -> str:
    return (
        f"{panel_kwh(series.today_kwh)} / "
        f"{panel_kwh(series.yesterday_kwh)} kWh"
    )


def panel_kwh(value: float) -> str:
    return f"{value:.1f}" if abs(value) >= 1 else f"{value:.2f}"


def panel_y_max(series: EnergyPlotSeries) -> float:
    values = [point.kw for point in (*series.yesterday, *series.today)]
    return rounded_axis_max(max(values, default=0.0))


def rounded_axis_max(value: float) -> float:
    if value <= 0:
        return 1.0
    raw_step = value / 4
    exponent = 10 ** math.floor(math.log10(raw_step))
    normalized = raw_step / exponent
    nice = next(candidate for candidate in (1, 2, 2.5, 5, 10) if normalized <= candidate)
    step = nice * exponent
    return max(step, math.ceil(value / step) * step)


def plot_point(
    point: AveragePowerPoint,
    plot: tuple[int, int, int, int],
    maximum: float,
) -> tuple[int, int]:
    x0, y0, x1, y1 = plot
    x = x0 + min(24.0, max(0.0, point.hour)) / 24 * (x1 - x0)
    y = y1 - min(maximum, max(0.0, point.kw)) / maximum * (y1 - y0)
    return round(x), round(y)


def draw_series_line(
    draw: ImageDraw.ImageDraw,
    points: Sequence[AveragePowerPoint],
    plot: tuple[int, int, int, int],
    maximum: float,
    fill: int,
    width: int,
    end_marker: bool,
) -> None:
    segments = power_line_segments(points)
    for segment in segments:
        coordinates = [plot_point(point, plot, maximum) for point in segment]
        if len(coordinates) >= 2:
            draw.line(coordinates, fill=fill, width=width, joint="curve")
    if end_marker and points:
        x, y = plot_point(points[-1], plot, maximum)
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=BLACK)


def power_line_segments(
    points: Sequence[AveragePowerPoint],
) -> tuple[tuple[AveragePowerPoint, ...], ...]:
    """Split samples when at least one normal 15-minute interval is missing."""

    if not points:
        return ()
    segments: list[list[AveragePowerPoint]] = [[points[0]]]
    for point in points[1:]:
        previous = segments[-1][-1]
        gap = point.timestamp - previous.timestamp
        adjacent = timedelta(0) < gap <= timedelta(minutes=15)
        if not adjacent:
            segments.append([])
        segments[-1].append(point)
    return tuple(tuple(segment) for segment in segments)


def draw_daily_bars(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    values: Sequence[DailyEnergyValue],
    unit: str,
    today,
) -> None:
    x0, y0, x1, y1 = box
    draw.text((x0 + 15, y0 + 13), title, font=FONTS["chart_title"], fill=BLACK)
    visible = tuple(values[-DISPLAY_DAYS:])
    plot = (x0 + 18, y0 + 72, x1 - 14, y1 - 48)
    px0, py0, px1, py1 = plot
    maximum = rounded_axis_max(max((item.value for item in visible), default=0.0))
    draw.line((px0, py0, px0, py1), fill=FRAME_GRAY, width=2)
    draw.line((px0, py1, px1, py1), fill=FRAME_GRAY, width=2)
    if not visible:
        draw.text((px0, py0 + 20), "No data", font=FONTS["note"], fill=TEXT_GRAY)
        return

    slot = (px1 - px0) / len(visible)
    bar_width = min(38, max(20, round(slot * 0.58)))
    for index, item in enumerate(visible):
        center = px0 + slot * (index + 0.5)
        height = item.value / maximum * (py1 - py0) if maximum else 0
        left = round(center - bar_width / 2)
        right = round(center + bar_width / 2)
        top = round(py1 - height)
        is_today = item.day == today
        if is_today:
            draw.rectangle((left, top, right, py1), fill=PARTIAL_BAR_GRAY, outline=BLACK, width=3)
            draw_hatching(draw, (left, top, right, py1))
        else:
            fill = COMPLETED_BAR_GRAY if item.complete else YESTERDAY_GRAY
            draw.rectangle((left, top, right, py1), fill=fill)
        value_label = compact_value(item.value, unit)
        value_width, _ = text_wh(draw, value_label, FONTS["chart_value"])
        draw.text(
            (center - value_width / 2, max(py0, top - 24)),
            value_label,
            font=FONTS["chart_value"],
            fill=BLACK,
        )
        day_label = item.day.strftime("%a")[:2]
        if is_today:
            day_label += "*"
        day_width, _ = text_wh(draw, day_label, FONTS["chart_day"])
        draw.text((center - day_width / 2, py1 + 8), day_label, font=FONTS["chart_day"], fill=BLACK)

    axis_label = f"max {compact_number(maximum)} {unit}"
    axis_width, _ = text_wh(draw, axis_label, FONTS["chart_day"])
    draw.text((px1 - axis_width, y0 + 47), axis_label, font=FONTS["chart_day"], fill=TEXT_GRAY)


def draw_hatching(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    for start in range(x0 - (y1 - y0), x1 + 1, 10):
        begin_x = max(x0, start)
        begin_y = y1 - (begin_x - start)
        end_x = min(x1, start + (y1 - y0))
        end_y = y1 - (end_x - start)
        draw.line((begin_x, begin_y, end_x, end_y), fill=FRAME_GRAY, width=1)


def draw_footer(draw: ImageDraw.ImageDraw, data: EnergyDashboardData) -> None:
    source = "SAMPLE DATA" if data.source == "mock" else data.source.upper().replace("-", " ")
    freshness = ""
    if data.freshness is not None:
        freshness = data.freshness.astimezone(ZoneInfo(data.timezone)).strftime(
            " • updated %-I:%M %p"
        )
    message = f"{source}{freshness}  •  * Today is partial"
    if data.message and data.source != "mock":
        message = f"{message}  •  {data.message}"
    draw.text((SUMMARY_LEFT, 1271), message, font=FONTS["note"], fill=TEXT_GRAY)


def compact_number(value: float) -> str:
    if value >= 10:
        return f"{value:.0f}"
    if value >= 1:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.2f}".rstrip("0").rstrip(".")


def compact_value(value: float, unit: str) -> str:
    precision = 1 if value >= 10 or unit == "hr" else 1
    return f"{value:.{precision}f}"


def write_energy_output(image: Image.Image, path: Path | None = None) -> Path:
    output = path or OUT / "energy-dashboard.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    quantized = quantize_grayscale_4bit(image)
    output.write_bytes(encode_png_grayscale_4bit(quantized))
    return output


def render() -> None:
    print(write_energy_output(render_energy_image()))


if __name__ == "__main__":
    render()
