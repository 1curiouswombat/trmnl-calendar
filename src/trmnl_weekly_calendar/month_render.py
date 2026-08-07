from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from PIL import Image, ImageDraw

from trmnl_weekly_calendar.calendar_data import MonthEvent
from trmnl_weekly_calendar.render import (
    CURRENT_DAY_FILL,
    F,
    H,
    W,
    draw_centered,
    draw_weather_icon,
    ellipsize,
    localize_now,
    rounded_rect,
    start_of_week,
    text_wh,
    wrap,
)


MONTH_WEEK_ROWS = 4
MONTH_EVENT_ROW_H = 31
MONTH_EVENT_ROW_GAP = 6
MONTH_EVENT_LINE_STEP = 24
MONTH_EVENT_MAX_LINES = 3
MONTH_EVENT_TOP_OFFSET = 52
MONTH_TODAY_EVENT_TOP_OFFSET = 58
MONTH_EVENT_TIME_GAP = 6
MONTH_WEATHER_ICON_SCALE = 0.4
MONTH_WEATHER_RIGHT_PAD = 16
MONTH_WEATHER_ICON_GAP = 5
MONTH_BOUNDARY_LABEL_FILL = 136
MONTH_BOUNDARY_LABEL_GAP = 14

MonthWeather = dict[date, tuple[str, str]]


def render_month_image(
    *,
    view_start: date | None = None,
    month_start: date | None = None,
    events: list[MonthEvent] | None = None,
    weather: MonthWeather | None = None,
    now: datetime | None = None,
) -> Image.Image:
    local_now = localize_now(now)
    view_start = view_start or start_of_week(month_start or local_now.date())
    events = events or []
    weather = weather or {}

    img = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(img)

    margin_x = 58
    top = 30
    dow_h = 54
    grid_left = margin_x
    grid_right = W - margin_x
    grid_top = top + dow_h
    grid_bottom = H - 42
    col_w = (grid_right - grid_left) / 7
    row_h = (grid_bottom - grid_top) / MONTH_WEEK_ROWS
    col_edges = [round(grid_left + i * col_w) for i in range(8)]
    row_edges = [round(grid_top + i * row_h) for i in range(MONTH_WEEK_ROWS + 1)]
    cell_pad = 12

    for y in range(0, H, 7):
        draw.line((0, y, W, y), fill=253, width=1)

    for i, label in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")):
        draw_centered(draw, (col_edges[i], top, col_edges[i + 1], grid_top), label, F["day"], fill=0)

    today = local_now.date()
    events_by_day: dict[date, list[MonthEvent]] = defaultdict(list)
    for event in events:
        events_by_day[event.day].append(event)

    for row in range(MONTH_WEEK_ROWS):
        for col in range(7):
            cell_day = view_start + timedelta(days=row * 7 + col)
            x0 = col_edges[col]
            x1 = col_edges[col + 1]
            y0 = row_edges[row]
            y1 = row_edges[row + 1]
            is_today = cell_day == today

            if is_today:
                draw.rectangle((x0 + 2, y0 + 2, x1 - 2, y1 - 2), fill=CURRENT_DAY_FILL)

            draw_day_number(draw, cell_day, x0, y0, is_today)
            draw_month_weather(draw, weather.get(cell_day), x0, y0, x1)
            draw_month_events(draw, events_by_day.get(cell_day, []), x0, y0, x1, y1, cell_pad, is_today)

    return img


def draw_day_number(
    draw: ImageDraw.ImageDraw,
    cell_day: date,
    x0: int,
    y0: int,
    is_today: bool,
) -> None:
    label = str(cell_day.day)
    month_label = month_boundary_label(cell_day)
    date_font = F["current"]
    text_x = x0 + 18
    text_y = y0 + 8
    if is_today:
        text_x = x0 + 20
        bbox = draw.textbbox((text_x, text_y), label, font=date_font)
        rounded_rect(draw, (bbox[0] - 7, bbox[1] - 4, bbox[2] + 8, bbox[3] + 6), 3, fill=0)
        draw.text((text_x, text_y), label, font=date_font, fill=255)
        draw_month_boundary_label(draw, month_label, bbox[2] + MONTH_BOUNDARY_LABEL_GAP, text_y, date_font)
        return
    draw.text((text_x, text_y), label, font=date_font, fill=0)
    label_w, _label_h = text_wh(draw, label, date_font)
    draw_month_boundary_label(draw, month_label, text_x + label_w + MONTH_BOUNDARY_LABEL_GAP, text_y, date_font)


def month_boundary_label(cell_day: date) -> str:
    if cell_day.day != 1:
        return ""
    return cell_day.strftime("%b").upper()


def draw_month_boundary_label(
    draw: ImageDraw.ImageDraw,
    label: str,
    x: float,
    y: float,
    date_font,
) -> None:
    if not label:
        return
    boundary_font = F["month_boundary"]
    date_ascent, _date_descent = date_font.getmetrics()
    boundary_ascent, _boundary_descent = boundary_font.getmetrics()
    draw.text((x, y + date_ascent - boundary_ascent), label, font=boundary_font, fill=MONTH_BOUNDARY_LABEL_FILL)


def draw_month_weather(
    draw: ImageDraw.ImageDraw,
    weather: tuple[str, str] | None,
    x0: int,
    y0: int,
    x1: int,
) -> None:
    if weather is None:
        return

    kind, temp = weather
    temp_label = compact_temperature_label(temp)
    if not temp_label:
        return

    temp_w, _temp_h = text_wh(draw, temp_label, F["month_event"])
    text_x = x1 - MONTH_WEATHER_RIGHT_PAD - temp_w
    text_y = y0 + 10
    icon_x = round(text_x - MONTH_WEATHER_ICON_GAP - 18)
    draw_weather_icon(draw, kind, icon_x, y0 + 25, MONTH_WEATHER_ICON_SCALE, fill=0)
    draw.text((text_x, text_y), temp_label, font=F["month_event"], fill=0)


def compact_temperature_label(temp: str) -> str:
    label = temp.replace(" ", "")
    return "" if "--" in label else label


def draw_month_events(
    draw: ImageDraw.ImageDraw,
    events: list[MonthEvent],
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    cell_pad: int,
    is_today: bool = False,
) -> None:
    if not events:
        return

    event_top = y0 + (MONTH_TODAY_EVENT_TOP_OFFSET if is_today else MONTH_EVENT_TOP_OFFSET)
    available_h = y1 - event_top - 14
    max_rows = max(1, int((available_h + MONTH_EVENT_ROW_GAP) // (MONTH_EVENT_ROW_H + MONTH_EVENT_ROW_GAP)))
    visible = events[:max_rows]
    hidden_count = len(events) - len(visible)
    if hidden_count and max_rows > 1:
        visible = events[: max_rows - 1]
        hidden_count = len(events) - len(visible)

    text_x = x0 + cell_pad + 8
    max_w = x1 - text_x - cell_pad - 8
    line_counts = month_event_line_counts(draw, visible, max_w, available_h, bool(hidden_count))

    row_y = event_top
    for event, line_count in zip(visible, line_counts):
        row_h = month_event_row_height(line_count)
        lines = month_event_lines(draw, event, max_w, line_count)
        lines_h = line_count * MONTH_EVENT_LINE_STEP
        text_y = row_y + (row_h - lines_h) / 2
        for time_label, title in lines:
            draw_month_event_line(draw, text_x, text_y - 2, time_label, title, max_w)
            text_y += MONTH_EVENT_LINE_STEP
        row_y += row_h + MONTH_EVENT_ROW_GAP

    if hidden_count:
        more = f"+{hidden_count} more"
        clipped = ellipsize(draw, more, F["month_tiny"], max_w)
        draw.text((text_x, min(row_y + 1, y1 - 30)), clipped, font=F["month_tiny"], fill=70)


def month_event_line_counts(
    draw: ImageDraw.ImageDraw,
    events: list[MonthEvent],
    max_w: int,
    available_h: float,
    has_more: bool,
) -> list[int]:
    line_counts = [1 for _event in events]
    more_h = MONTH_EVENT_ROW_H + MONTH_EVENT_ROW_GAP if has_more else 0
    used_h = month_events_height(line_counts) + more_h
    spare_h = available_h - used_h
    if spare_h < MONTH_EVENT_LINE_STEP:
        return line_counts

    for index, event in enumerate(events):
        if month_event_width(draw, event) <= max_w:
            continue
        wrapped = month_event_wrapped_lines(draw, event, max_w)
        target_lines = min(MONTH_EVENT_MAX_LINES, max(1, len(wrapped)))
        while line_counts[index] < target_lines and spare_h >= MONTH_EVENT_LINE_STEP:
            line_counts[index] += 1
            spare_h -= MONTH_EVENT_LINE_STEP
    return line_counts


def month_events_height(line_counts: list[int]) -> int:
    if not line_counts:
        return 0
    return sum(month_event_row_height(line_count) for line_count in line_counts) + MONTH_EVENT_ROW_GAP * (len(line_counts) - 1)


def month_event_row_height(line_count: int) -> int:
    return MONTH_EVENT_ROW_H + (line_count - 1) * MONTH_EVENT_LINE_STEP


def month_event_lines(
    draw: ImageDraw.ImageDraw,
    event: MonthEvent,
    max_w: int,
    max_lines: int,
) -> list[tuple[str, str]]:
    if max_lines <= 1:
        return month_event_ellipsized_line(draw, event, max_w)

    lines = month_event_wrapped_lines(draw, event, max_w)
    if len(lines) <= max_lines:
        return lines

    clipped = lines[:max_lines]
    remainder = " ".join(title for _time_label, title in lines[max_lines - 1 :] if title).strip()
    clipped[-1] = ("", ellipsize(draw, remainder, F["month_event"], max_w))
    return clipped


def month_event_width(draw: ImageDraw.ImageDraw, event: MonthEvent) -> int:
    title = event.title.strip()
    time_label = event.time_label.strip()
    title_w = text_wh(draw, title, F["month_event"])[0] if title else 0
    if not time_label:
        return title_w
    time_w = text_wh(draw, time_label, F["month_time"])[0]
    return time_w + (MONTH_EVENT_TIME_GAP if title else 0) + title_w


def month_event_wrapped_lines(
    draw: ImageDraw.ImageDraw,
    event: MonthEvent,
    max_w: int,
) -> list[tuple[str, str]]:
    title = event.title.strip()
    time_label = event.time_label.strip()
    if not time_label:
        return [("", line) for line in wrap(draw, title, F["month_event"], max_w)] or [("", "")]
    if month_event_width(draw, event) <= max_w:
        return [(time_label, title)]

    words = title.split()
    lines: list[tuple[str, str]] = []
    time_w = text_wh(draw, time_label, F["month_time"])[0]
    first_line_w = max_w - time_w - (MONTH_EVENT_TIME_GAP if title else 0)
    first_words: list[str] = []
    while words and first_line_w > 0:
        trial = words[0] if not first_words else f"{' '.join(first_words)} {words[0]}"
        if text_wh(draw, trial, F["month_event"])[0] > first_line_w:
            break
        first_words.append(words.pop(0))

    lines.append((time_label, " ".join(first_words)))
    remaining = " ".join(words)
    if remaining:
        lines.extend(("", line) for line in wrap(draw, remaining, F["month_event"], max_w))
    return lines


def month_event_ellipsized_line(
    draw: ImageDraw.ImageDraw,
    event: MonthEvent,
    max_w: int,
) -> list[tuple[str, str]]:
    title = event.title.strip()
    time_label = event.time_label.strip()
    if not time_label:
        return [("", ellipsize(draw, title, F["month_event"], max_w))]

    clipped_time = ellipsize(draw, time_label, F["month_time"], max_w)
    time_w = text_wh(draw, clipped_time, F["month_time"])[0]
    title_w = max_w - time_w - MONTH_EVENT_TIME_GAP
    if not title or title_w <= 0:
        return [(clipped_time, "")]
    return [(clipped_time, ellipsize(draw, title, F["month_event"], title_w))]


def draw_month_event_line(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    time_label: str,
    title: str,
    max_w: int,
) -> None:
    if not time_label:
        draw.text((x, y), ellipsize(draw, title, F["month_event"], max_w), font=F["month_event"], fill=0)
        return

    clipped_time = ellipsize(draw, time_label, F["month_time"], max_w)
    draw.text((x, y), clipped_time, font=F["month_time"], fill=0)
    time_w = text_wh(draw, clipped_time, F["month_time"])[0]
    title_x = x + time_w + MONTH_EVENT_TIME_GAP
    title_w = max_w - time_w - MONTH_EVENT_TIME_GAP
    if title and title_w > 0:
        draw.text((title_x, y), ellipsize(draw, title, F["month_event"], title_w), font=F["month_event"], fill=0)
