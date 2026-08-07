from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, features

try:
    from trmnl_weekly_calendar.constants import WEEKLY_DAY_COUNT
except ModuleNotFoundError:
    from constants import WEEKLY_DAY_COUNT

try:
    from trmnl_weekly_calendar.png_encode import encode_png_grayscale_4bit, quantize_grayscale_4bit
except ModuleNotFoundError:
    from png_encode import encode_png_grayscale_4bit, quantize_grayscale_4bit


W, H = 1872, 1404
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT = PROJECT_ROOT / "outputs"
FONT_DIR = PROJECT_ROOT / "assets" / "fonts"
MOCK_WEEK_START = date(2026, 6, 7)

ROBOTO_SERIF = FONT_DIR / "RobotoSerif.ttf"
ROBOTO_FLEX = FONT_DIR / "RobotoFlex.ttf"
CLIMACONS = FONT_DIR / "climacons-webfont.ttf"
LIBERATION_SANS = Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")
LIBERATION_SANS_BOLD = Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")
DAY_START_HOUR = 6.0
DAY_END_HOUR = 20.0
CURRENT_DAY_FILL = 238
CURRENT_DAY_WIDTH_WEIGHT = 1.16
MAX_EVENT_FILL_GRAY = 226
EVENT_ACCENT_WIDTH = 8
EVENT_TEXT_GAP_Y = 8
EVENT_TEXT_OFFSET_X = 14
MIN_EVENT_ACCENT_HEIGHT = 18


CSS_PX_TO_POINTS = 72 / 96
FONT_LAYOUT_ENGINE = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
VARIATION_WEIGHTS = {
    "Thin": 100,
    "ExtraLight": 200,
    "Light": 300,
    "Regular": 400,
    "Medium": 500,
    "SemiBold": 600,
    "Bold": 700,
    "ExtraBold": 800,
    "Black": 900,
    "ExtraBlack": 1000,
}


def axis_name(axis: dict[str, object]) -> str:
    name = axis.get("name", "")
    if isinstance(name, bytes):
        return name.decode("utf-8", "replace")
    return str(name)


def clamp_axis_value(value: float, axis: dict[str, object]) -> float:
    minimum = float(axis["minimum"])
    maximum = float(axis["maximum"])
    return min(maximum, max(minimum, value))


def variation_weight(variation: str | None) -> int | None:
    if not variation:
        return None
    return VARIATION_WEIGHTS.get(variation.removesuffix(" Italic"))


def apply_optical_size(
    loaded: ImageFont.FreeTypeFont,
    size: int,
    variation: str | None,
    optical_size: float | None = None,
) -> None:
    try:
        axes = loaded.get_variation_axes()
    except OSError:
        return

    weight = variation_weight(variation)
    values: list[float] = []
    for axis in axes:
        value = float(axis["default"])
        name = axis_name(axis)
        if name == "Optical Size":
            target_optical_size = optical_size if optical_size is not None else float(size) * CSS_PX_TO_POINTS
            value = clamp_axis_value(target_optical_size, axis)
        elif name == "Weight" and weight is not None:
            value = clamp_axis_value(float(weight), axis)
        values.append(value)

    try:
        loaded.set_variation_by_axes(values)
    except OSError:
        return


def apply_browser_optical_size(loaded: ImageFont.FreeTypeFont, size: int, variation: str | None) -> None:
    apply_optical_size(loaded, size, variation)


def font(
    path: str | Path,
    size: int,
    variation: str | None = None,
    *,
    browser_optical_size: bool = False,
    optical_size: float | None = None,
) -> ImageFont.FreeTypeFont:
    loaded = ImageFont.truetype(str(path), size, layout_engine=FONT_LAYOUT_ENGINE)
    if variation:
        try:
            loaded.set_variation_by_name(variation)
        except OSError:
            pass
    if optical_size is not None:
        apply_optical_size(loaded, size, variation, optical_size)
    elif browser_optical_size:
        apply_browser_optical_size(loaded, size, variation)
    return loaded


def first_existing_font(candidates: tuple[Path, ...], fallback: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return fallback


MONTH_EVENT_FONT = first_existing_font((LIBERATION_SANS,), ROBOTO_FLEX)
MONTH_EVENT_OPTICAL_SIZE = 14 if MONTH_EVENT_FONT == ROBOTO_FLEX else None
MONTH_TIME_FONT = first_existing_font((LIBERATION_SANS_BOLD,), ROBOTO_FLEX)
MONTH_TIME_OPTICAL_SIZE = 14 if MONTH_TIME_FONT == ROBOTO_FLEX else None


F = {
    "title": font(ROBOTO_SERIF, 58, "Bold"),
    "meta": font(ROBOTO_FLEX, 25, "Regular", browser_optical_size=True),
    "day": font(ROBOTO_SERIF, 36, "Bold"),
    "date": font(ROBOTO_SERIF, 31, "Regular"),
    "weather": font(ROBOTO_FLEX, 26, "Regular", browser_optical_size=True),
    "time": font(ROBOTO_SERIF, 27, "Bold"),
    "event": font(MONTH_EVENT_FONT, 26, "Regular", optical_size=MONTH_EVENT_OPTICAL_SIZE),
    "event_small": font(MONTH_EVENT_FONT, 22, "Regular", optical_size=MONTH_EVENT_OPTICAL_SIZE),
    "current": font(ROBOTO_SERIF, 31, "Bold"),
    "now": font(ROBOTO_FLEX, 22, "Regular", browser_optical_size=True),
    "tiny": font(MONTH_EVENT_FONT, 20, "Regular", optical_size=MONTH_EVENT_OPTICAL_SIZE),
    "month_event": font(MONTH_EVENT_FONT, 22, "Regular", optical_size=MONTH_EVENT_OPTICAL_SIZE),
    "month_time": font(MONTH_TIME_FONT, 22, "Bold", optical_size=MONTH_TIME_OPTICAL_SIZE),
    "month_tiny": font(MONTH_EVENT_FONT, 20, "Regular", optical_size=MONTH_EVENT_OPTICAL_SIZE),
    "month_boundary": font(ROBOTO_SERIF, 28, "Bold"),
}


def configured_zone() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TRMNL_TIMEZONE", "America/Denver"))


def configured_now() -> datetime:
    return datetime.now(configured_zone())


def localize_now(now: datetime | None = None) -> datetime:
    zone = configured_zone()
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def configured_today() -> date:
    return localize_now().date()


@dataclass
class Event:
    day: int
    start: float
    end: float
    title: str
    where: str = ""
    tone: int = 232


@dataclass
class AllDayEvent:
    start_day: int
    end_day: int
    title: str
    row: int = 0
    tone: int = 238


@dataclass(frozen=True)
class TimedEventLayout:
    event: Event
    visible_start: float
    visible_end: float
    lane: int
    lane_count: int


@dataclass(frozen=True)
class TimedEventBox:
    layout: TimedEventLayout
    x0: float
    y0: float
    x1: float
    y1: float
    text_y1: float


MOCK_DAYS = [
    ("SUN", "7", "clear", "72 / 51"),
    ("MON", "8", "cloud", "68 / 49"),
    ("TUE", "9", "rain", "61 / 47"),
    ("WED", "10", "partly", "70 / 50"),
    ("THU", "11", "wind", "76 / 54"),
]

MOCK_ALL_DAY_EVENTS = [
    AllDayEvent(1, 2, "Alex PTO", 0, 238),
    AllDayEvent(3, 3, "Quarterly planning", 0, 232),
    AllDayEvent(3, 3, "School forms due", 1, 244),
    AllDayEvent(4, 4, "Trash + recycling", 0, 244),
    AllDayEvent(5, 6, "Camping prep", 0, 232),
]

MOCK_EVENTS = [
    Event(0, 8.5, 9.5, "Coffee + reading", "Kitchen table", 240),
    Event(0, 16.0, 17.0, "Meal prep", "Home", 235),
    Event(1, 9.0, 10.0, "Weekly planning", "Studio", 232),
    Event(1, 13.0, 14.25, "Product review", "Video", 238),
    Event(1, 17.5, 18.5, "Walk", "Park loop", 242),
    Event(2, 8.0, 9.0, "School dropoff", "", 238),
    Event(2, 10.5, 12.0, "Deep work block", "Desk", 228),
    Event(2, 15.0, 16.0, "Dentist", "Pearl St.", 240),
    Event(3, 7.5, 8.25, "Gym", "", 238),
    Event(3, 11.0, 12.0, "Design critique", "Room 3B", 232),
    Event(3, 18.0, 20.0, "Dinner with Sam", "Northside", 240),
    Event(4, 9.5, 10.25, "1:1 Morgan", "Phone", 235),
    Event(4, 12.0, 13.0, "Lunch outside", "", 244),
    Event(4, 14.0, 16.0, "Prototype pass", "Workshop", 229),
    Event(5, 8.0, 8.75, "Run", "Creek trail", 242),
    Event(5, 10.0, 11.0, "Finance check-in", "Video", 235),
    Event(5, 13.0, 15.0, "Calendar display sketch", "TRMNL", 226),
    Event(5, 19.0, 20.0, "Pick up groceries", "", 241),
    Event(6, 9.0, 10.0, "Farmers market", "Downtown", 238),
    Event(6, 11.5, 13.0, "House projects", "", 234),
    Event(6, 19.0, 21.0, "Movie night", "Living room", 240),
]


def text_wh(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def draw_centered(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    fnt: ImageFont.ImageFont,
    fill: int = 0,
    y_offset: int = 0,
) -> None:
    x0, y0, x1, y1 = box
    tb = draw.textbbox((0, 0), text, font=fnt)
    cx = x0 + (x1 - x0) / 2
    cy = y0 + (y1 - y0) / 2 + y_offset
    draw.text((cx - (tb[0] + tb[2]) / 2, cy - (tb[1] + tb[3]) / 2), text, font=fnt, fill=fill)


def rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float, float, float],
    radius: int,
    fill: int | None = None,
    outline: int | None = None,
    width: int = 1,
) -> None:
    draw.rounded_rectangle(tuple(round(v) for v in xy), radius=radius, fill=fill, outline=outline, width=width)


def event_fill(tone: int) -> int:
    return min(MAX_EVENT_FILL_GRAY, max(0, tone))


def event_accent(tone: int) -> int:
    return min(80, max(0, tone - 170))


def draw_hatching(img: Image.Image, xy: tuple[int, int, int, int], step: int = 11, fill: int = 206) -> None:
    x0, y0, x1, y1 = xy
    w, h = x1 - x0, y1 - y0
    patch = Image.new("L", (w, h), 255)
    mask = Image.new("L", (w, h), 0)
    pdraw = ImageDraw.Draw(patch)
    mdraw = ImageDraw.Draw(mask)
    for x in range(-h, w, step):
        pdraw.line((x, h, x + h, 0), fill=fill, width=1)
        mdraw.line((x, h, x + h, 0), fill=255, width=1)
    img.paste(patch, (x0, y0), mask)


def draw_weather_icon(
    draw: ImageDraw.ImageDraw,
    kind: str,
    cx: int,
    cy: int,
    scale: float = 1.0,
    fill: int = 0,
) -> None:
    glyphs = {
        "clear": "\ue028",  # climacon.sun
        "cloud": "\ue000",  # climacon.cloud
        "rain": "\ue003",  # climacon.rain.cloud
        "partly": "\ue001",  # climacon.cloud.sun
        "wind": "\ue021",  # climacon.wind
        "storm": "\ue025",  # climacon.lightning.cloud
        "snow": "\ue018",  # climacon.snow.cloud
        "fog": "\ue01b",  # climacon.fog.cloud
    }
    glyph = glyphs.get(kind, "\ue000")
    if not CLIMACONS.exists():
        draw_centered(draw, (cx - 42, cy - 34, cx + 42, cy + 34), "?", F["day"], fill=fill)
        return
    icon_font = ImageFont.truetype(str(CLIMACONS), max(18, int(76 * scale)))
    bbox = draw.textbbox((0, 0), glyph, font=icon_font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), glyph, font=icon_font, fill=fill)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = word if not cur else f"{cur} {word}"
        if text_wh(draw, trial, fnt)[0] <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def ellipsize(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont, max_w: int) -> str:
    if text_wh(draw, text, fnt)[0] <= max_w:
        return text
    while text and text_wh(draw, text + "...", fnt)[0] > max_w:
        text = text[:-1]
    return text.rstrip() + "..."


def short_clock(hour: float) -> str:
    h = int(hour)
    m = int(round((hour - h) * 60))
    if m == 0:
        return str(h % 12 or 12)
    return f"{h % 12 or 12}:{m:02d}"


def time_y(hour: float, top: int, bottom: int) -> float:
    return top + (hour - DAY_START_HOUR) / (DAY_END_HOUR - DAY_START_HOUR) * (bottom - top)


def day_column_edges(left: int, right: int, highlighted_day: int) -> list[int]:
    weights = [1.0] * WEEKLY_DAY_COUNT
    if 0 <= highlighted_day < WEEKLY_DAY_COUNT:
        weights[highlighted_day] = CURRENT_DAY_WIDTH_WEIGHT

    total_weight = sum(weights)
    x = float(left)
    edges = [left]
    for weight in weights[:-1]:
        x += (right - left) * weight / total_weight
        edges.append(round(x))
    edges.append(right)
    return edges


def start_of_week(today: date | None = None, first_weekday: int = 6) -> date:
    """Return the week start for today. Defaults to Sunday-first calendars."""
    today = today or configured_today()
    days_since_start = (today.weekday() - first_weekday) % 7
    return today - timedelta(days=days_since_start)


def days_for_week(week_start: date) -> list[tuple[str, str, str, str]]:
    weather = [("clear", "72 / 51"), ("cloud", "68 / 49"), ("rain", "61 / 47"), ("partly", "70 / 50"), ("wind", "76 / 54"), ("clear", "80 / 57"), ("storm", "73 / 52")]
    days = []
    for i in range(WEEKLY_DAY_COUNT):
        kind, temp = weather[i % len(weather)]
        day = week_start + timedelta(days=i)
        days.append((day.strftime("%a").upper(), str(day.day), kind, temp))
    return days


def allocate_all_day_rows(events: list[AllDayEvent]) -> list[AllDayEvent]:
    occupied_until_by_row: list[int] = []
    allocated: list[AllDayEvent] = []
    for event in sorted(events, key=lambda ev: (ev.start_day, ev.end_day, ev.title)):
        start_day = max(0, event.start_day)
        end_day = min(WEEKLY_DAY_COUNT - 1, event.end_day)
        if start_day > end_day:
            continue
        row = 0
        while row < len(occupied_until_by_row) and occupied_until_by_row[row] >= start_day:
            row += 1
        if row == len(occupied_until_by_row):
            occupied_until_by_row.append(end_day)
        else:
            occupied_until_by_row[row] = end_day
        allocated.append(AllDayEvent(start_day, end_day, event.title, row, event.tone))
    return allocated


def allocate_timed_event_lanes(events: list[Event]) -> list[TimedEventLayout]:
    visible_by_day: list[list[tuple[Event, float, float]]] = [[] for _ in range(WEEKLY_DAY_COUNT)]
    for event in events:
        if not 0 <= event.day < WEEKLY_DAY_COUNT:
            continue
        visible_start = max(DAY_START_HOUR, event.start)
        visible_end = min(DAY_END_HOUR, event.end)
        if visible_end <= DAY_START_HOUR or visible_start >= DAY_END_HOUR or visible_end <= visible_start:
            continue
        visible_by_day[event.day].append((event, visible_start, visible_end))

    layouts: list[TimedEventLayout] = []
    for day_events in visible_by_day:
        current_group: list[tuple[Event, float, float]] = []
        current_group_end = DAY_START_HOUR
        for item in sorted(day_events, key=lambda item: (item[1], item[2], item[0].title)):
            _event, visible_start, visible_end = item
            if current_group and visible_start >= current_group_end:
                layouts.extend(allocate_timed_event_group(current_group))
                current_group = []
                current_group_end = DAY_START_HOUR
            current_group.append(item)
            current_group_end = max(current_group_end, visible_end)
        if current_group:
            layouts.extend(allocate_timed_event_group(current_group))

    return sorted(
        layouts,
        key=lambda layout: (
            layout.event.day,
            layout.visible_start,
            layout.visible_end,
            layout.event.title,
        ),
    )


def allocate_timed_event_group(group: list[tuple[Event, float, float]]) -> list[TimedEventLayout]:
    lane_ends: list[float] = []
    assigned: list[tuple[Event, float, float, int]] = []
    for event, visible_start, visible_end in group:
        lane = 0
        while lane < len(lane_ends) and lane_ends[lane] > visible_start:
            lane += 1
        if lane == len(lane_ends):
            lane_ends.append(visible_end)
        else:
            lane_ends[lane] = visible_end
        assigned.append((event, visible_start, visible_end, lane))

    lane_count = max(1, len(lane_ends))
    return [
        TimedEventLayout(event, visible_start, visible_end, lane, lane_count)
        for event, visible_start, visible_end, lane in assigned
    ]


def horizontal_ranges_overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
    return min(a1, b1) - max(a0, b0) > 1


def timed_event_boxes(
    layouts: list[TimedEventLayout],
    col_edges: list[int],
    event_inset_x: int,
    event_lane_gap: int,
    grid_top: int,
    grid_bottom: int,
) -> list[TimedEventBox]:
    duration_boxes: list[TimedEventBox] = []
    for layout in layouts:
        ev = layout.event
        day_x0 = col_edges[ev.day] + event_inset_x
        day_x1 = col_edges[ev.day + 1] - event_inset_x
        usable_w = day_x1 - day_x0 - event_lane_gap * (layout.lane_count - 1)
        lane_w = usable_w / layout.lane_count
        x0 = day_x0 + layout.lane * (lane_w + event_lane_gap)
        x1 = x0 + lane_w
        y0 = time_y(layout.visible_start, grid_top, grid_bottom) + 2
        y1 = time_y(layout.visible_end, grid_top, grid_bottom) - 2
        if y1 - y0 < MIN_EVENT_ACCENT_HEIGHT:
            y1 = min(grid_bottom, y0 + MIN_EVENT_ACCENT_HEIGHT)
            if y1 - y0 < MIN_EVENT_ACCENT_HEIGHT:
                y0 = max(grid_top, y1 - MIN_EVENT_ACCENT_HEIGHT)
        duration_boxes.append(TimedEventBox(layout, x0, y0, x1, y1, grid_bottom))

    boxes: list[TimedEventBox] = []
    for box in duration_boxes:
        next_y0 = grid_bottom + EVENT_TEXT_GAP_Y
        for other in duration_boxes:
            if other is box:
                continue
            if other.layout.event.day != box.layout.event.day:
                continue
            if other.y0 <= box.y0 + 1:
                continue
            if not horizontal_ranges_overlap(box.x0, box.x1, other.x0, other.x1):
                continue
            next_y0 = min(next_y0, other.y0)
        text_y1 = min(grid_bottom, next_y0 - EVENT_TEXT_GAP_Y)
        boxes.append(
            TimedEventBox(
                box.layout,
                box.x0,
                box.y0,
                box.x1,
                box.y1,
                max(box.y0, text_y1),
            )
        )

    return boxes


def current_marker(week_start: date, now: datetime | None = None) -> tuple[int, float]:
    now = localize_now(now)
    week_end = week_start + timedelta(days=WEEKLY_DAY_COUNT - 1)
    if week_start <= now.date() <= week_end:
        day = (now.date() - week_start).days
        minutes = now.hour * 60 + now.minute + (1 if now.second >= 30 else 0)
        rounded = round(minutes / 15) * 15
        hour = rounded / 60
    else:
        day = min(WEEKLY_DAY_COUNT - 1, max(0, (now.date() - week_start).days))
        hour = DAY_START_HOUR
    return day, min(DAY_END_HOUR, max(DAY_START_HOUR, hour))


def time_label_for_width(
    draw: ImageDraw.ImageDraw,
    start: float,
    end: float,
    fnt: ImageFont.ImageFont,
    max_w: int,
) -> str:
    label = f"{short_clock(start)}-{short_clock(end)}"
    if text_wh(draw, label, fnt)[0] <= max_w:
        return label
    return ellipsize(draw, short_clock(start), fnt, max_w)


def draw_event_text(
    draw: ImageDraw.ImageDraw,
    box: TimedEventBox,
) -> None:
    layout = box.layout
    ev = layout.event
    text_x = box.x0 + EVENT_ACCENT_WIDTH + EVENT_TEXT_OFFSET_X
    text_y = box.y0 - 1
    max_w = max(1, round(box.x1 - text_x))
    available_h = box.text_y1 - text_y
    box_w = box.x1 - box.x0
    if max_w < 22 or available_h < 16:
        return

    compact_time = time_label_for_width(draw, layout.visible_start, layout.visible_end, F["tiny"], max_w)
    if layout.lane_count > 1 or box_w < 150:
        title_font = F["tiny"] if box_w < 104 else F["event_small"]
        meta_font = F["tiny"]
    else:
        title_font = F["event"]
        meta_font = F["event_small"]
        compact_time = time_label_for_width(draw, layout.visible_start, layout.visible_end, meta_font, max_w)

    if available_h < 58:
        compact = f"{compact_time} {ev.title}"
        line = ellipsize(draw, compact, meta_font, max_w)
        draw.text((text_x, text_y), line, font=meta_font, fill=0)
        return

    time_label = ellipsize(draw, compact_time, meta_font, max_w)
    draw.text((text_x, text_y), time_label, font=meta_font, fill=0)
    ty = text_y + (22 if meta_font is F["tiny"] else 25)
    line_h = 21 if title_font is F["tiny"] else 24 if title_font is F["event_small"] else 28
    max_title_lines = min(2, max(0, int((box.text_y1 - ty) // line_h)))
    for line in wrap(draw, ev.title, title_font, max_w)[:max_title_lines]:
        draw.text((text_x, ty), ellipsize(draw, line, title_font, max_w), font=title_font, fill=0)
        ty += line_h

    if ev.where and box.text_y1 - ty > 20:
        location = ellipsize(draw, ev.where, F["tiny"], max_w)
        draw.text((text_x, ty), location, font=F["tiny"], fill=70)


def draw_timed_event(draw: ImageDraw.ImageDraw, box: TimedEventBox) -> None:
    accent_x1 = min(box.x1, box.x0 + EVENT_ACCENT_WIDTH)
    rounded_rect(
        draw,
        (box.x0, box.y0, accent_x1, box.y1),
        3,
        fill=event_accent(box.layout.event.tone),
    )
    draw_event_text(draw, box)


def clipped_segments(
    x0: float,
    x1: float,
    blockers: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    segments = [(x0, x1)]
    for block_x0, block_x1 in sorted(blockers):
        next_segments: list[tuple[float, float]] = []
        for segment_x0, segment_x1 in segments:
            if block_x1 <= segment_x0 or block_x0 >= segment_x1:
                next_segments.append((segment_x0, segment_x1))
                continue
            if segment_x0 < block_x0:
                next_segments.append((segment_x0, block_x0))
            if block_x1 < segment_x1:
                next_segments.append((block_x1, segment_x1))
        segments = next_segments
    return segments


def draw_current_marker(
    draw: ImageDraw.ImageDraw,
    *,
    week_start: date,
    now: datetime,
    col_edges: list[int],
    card_inset_x: int,
    grid_top: int,
    grid_bottom: int,
    event_boxes: list[tuple[int, float, float, float, float]],
) -> None:
    marker_day, marker_hour = current_marker(week_start, now)
    marker_y = round(time_y(marker_hour, grid_top, grid_bottom))
    marker_x0 = col_edges[marker_day] + card_inset_x
    marker_x1 = col_edges[marker_day + 1] - card_inset_x
    marker_mid = round((marker_x0 + marker_x1) / 2)
    event_blockers = [
        (box_x0 - 3, box_x1 + 3)
        for day, box_x0, box_y0, box_x1, box_y1 in event_boxes
        if day == marker_day and box_y0 <= marker_y <= box_y1
    ]
    line_blockers = [*event_blockers, (marker_mid - 16, marker_mid + 16)]
    for segment_x0, segment_x1 in clipped_segments(
        marker_x0 + 8,
        marker_x1 - 8,
        line_blockers,
    ):
        if segment_x1 - segment_x0 >= 5:
            draw.line((segment_x0, marker_y, segment_x1, marker_y), fill=0, width=2)

    marker_mid_blocked = any(
        block_x0 <= marker_mid <= block_x1
        for block_x0, block_x1 in event_blockers
    )
    if not marker_mid_blocked:
        draw.ellipse(
            (marker_mid - 6, marker_y - 6, marker_mid + 6, marker_y + 6),
            fill=255,
            outline=0,
            width=2,
        )
        draw.ellipse(
            (marker_mid - 2, marker_y - 2, marker_mid + 2, marker_y + 2),
            fill=0,
        )

    for direction in (-1, 1):
        tip = marker_x0 + 2 if direction < 0 else marker_x1 - 2
        inner = tip + direction * 18
        wing = 7
        draw.polygon(
            [
                (tip, marker_y),
                (inner, marker_y - wing),
                (inner - direction * 4, marker_y),
                (inner, marker_y + wing),
            ],
            fill=0,
        )


def render_image(
    *,
    week_start: date = MOCK_WEEK_START,
    days: list[tuple[str, str, str, str]] | None = None,
    all_day_events: list[AllDayEvent] | None = None,
    events: list[Event] | None = None,
    now: datetime | None = None,
) -> Image.Image:
    local_now = localize_now(now)
    default_days = days_for_week(week_start)
    days = list(days[:WEEKLY_DAY_COUNT]) if days is not None else default_days
    if len(days) < WEEKLY_DAY_COUNT:
        days.extend(default_days[len(days) :])
    all_day_events = allocate_all_day_rows(all_day_events if all_day_events is not None else MOCK_ALL_DAY_EVENTS)
    events = events if events is not None else MOCK_EVENTS

    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)

    margin_left = 38
    margin_right = 42
    top = 46
    day_h = 286
    grid_top = top + day_h
    grid_bottom = H - 64
    time_w = 72
    grid_left = margin_left + time_w
    grid_right = W - margin_right
    current_day = local_now.date()
    highlighted_day = (
        (current_day - week_start).days
        if week_start <= current_day < week_start + timedelta(days=WEEKLY_DAY_COUNT)
        else -1
    )
    col_edges = day_column_edges(grid_left, grid_right, highlighted_day)
    card_inset_x = 10
    text_pad_x = 20

    # Fine paper grain.
    for y in range(0, H, 7):
        draw.line((0, y, W, y), fill=253, width=1)

    # Day headings with weather tucked beneath each printed date.
    day_top = top
    for i, (dow, date_label, _kind, _temp) in enumerate(days):
        dow, date_label, kind, temp = days[i]
        x0 = col_edges[i]
        x1 = col_edges[i + 1]
        if i == highlighted_day:
            draw.rectangle((x0 + 2, day_top + 2, x1 - 2, grid_bottom), fill=CURRENT_DAY_FILL)
            rounded_rect(draw, (x0 + 24, day_top + 24, x1 - 24, day_top + 74), 3, fill=0)
            draw_centered(draw, (x0, day_top + 24, x1, day_top + 74), f"{dow} {date_label}", F["day"], fill=255, y_offset=-1)
        else:
            draw_centered(draw, (x0, day_top + 24, x1, day_top + 74), f"{dow} {date_label}", F["day"], fill=0, y_offset=-1)
        draw_weather_icon(draw, kind, round((x0 + x1) / 2), day_top + 122, 0.74)
        draw_centered(draw, (x0, day_top + 165, x1, day_top + 198), temp, F["weather"], fill=0)

    # All-day event band.
    all_day_top = day_top + 212
    row_h = 34
    row_gap = 6
    for all_day in all_day_events:
        x0 = col_edges[all_day.start_day] + card_inset_x
        x1 = col_edges[all_day.end_day + 1] - card_inset_x
        y0 = all_day_top + all_day.row * (row_h + row_gap)
        y1 = y0 + row_h
        rounded_rect(draw, (x0, y0, x1, y1), 5, fill=event_fill(all_day.tone))
        label = ellipsize(draw, all_day.title, F["event"], round(x1 - x0 - text_pad_x))
        _, lh = text_wh(draw, label, F["event"])
        draw.text((x0 + text_pad_x / 2, y0 + (row_h - lh) / 2 - 2), label, font=F["event"], fill=0)
    # Time grid.
    for hour in range(int(DAY_START_HOUR), int(DAY_END_HOUR) + 1):
        y = round(time_y(hour, grid_top, grid_bottom))
        label = f"{hour % 12 or 12}{'a' if hour < 12 else 'p'}"
        tw, th = text_wh(draw, label, F["time"])
        draw.text((grid_left - 18 - tw, y - th / 2 - 2), label, font=F["time"], fill=0)

    # Events.
    event_lane_gap = 8
    event_boxes: list[tuple[int, float, float, float, float]] = []
    layouts = allocate_timed_event_lanes(events)
    for box in timed_event_boxes(layouts, col_edges, card_inset_x, event_lane_gap, grid_top, grid_bottom):
        draw_timed_event(draw, box)
        event_boxes.append(
            (
                box.layout.event.day,
                box.x0,
                box.y0,
                min(box.x1, box.x0 + EVENT_ACCENT_WIDTH),
                box.y1,
            )
        )

    draw_current_marker(
        draw,
        week_start=week_start,
        now=local_now,
        col_edges=col_edges,
        card_inset_x=card_inset_x,
        grid_top=grid_top,
        grid_bottom=grid_bottom,
        event_boxes=event_boxes,
    )
    return img


def write_outputs(img: Image.Image) -> tuple[Path, Path, Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    gray_path = OUT / "trmnl_weekly_calendar_mockup_grayscale.png"
    gray4_path = OUT / "trmnl_weekly_calendar_mockup_4bit_grayscale.png"
    bw_path = OUT / "trmnl_weekly_calendar_mockup_dithered.png"
    img.save(gray_path)
    gray4 = quantize_grayscale_4bit(img)
    gray4_path.write_bytes(encode_png_grayscale_4bit(gray4))
    bw = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    bw.save(bw_path)
    return gray_path, gray4_path, bw_path


def render() -> None:
    paths = write_outputs(render_image())
    for path in paths:
        print(path)


if __name__ == "__main__":
    render()
