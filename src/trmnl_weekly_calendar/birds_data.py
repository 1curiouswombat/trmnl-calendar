from __future__ import annotations

"""Deterministic bird-page data model.

This module is deliberately pure: it performs no network access, no
eBird API calls, no random selection, and no clock reads.  Its only
input is a dated snapshot (a JSON file produced elsewhere, for example
by a separate digest job that already applied the eBird skill rules)
plus the page date supplied by the caller.

That separation is what makes a merged plugin deterministic:

* the same snapshot + the same page date always select the same birds;
* a changed snapshot produces a different page (and therefore a
  different TRMNL fingerprint), never a silently different render of
  the same input;
* live eBird fetching, life-list reads, and illustration generation
  must never happen inside the renderer or the server request path.
  If they are ever added, they belong in a separate snapshot builder
  whose output is a dated JSON file, not in this module.

Selection rules (all ties broken explicitly, never by dict order or
by ``random``):

* Monthly: the snapshot's ranked top list, in snapshot order.
  Ties in day counts keep snapshot order, so the featured bird is
  stable.  The "also" list is the other ranked species, in order.
* Spotted here: not-yet-logged species (absent from ``life_list``)
  reported in the last 7 days, sorted by observation date descending,
  then distance ascending, then species name ascending.  Only if the
  7-day window contains no not-yet-logged species, fall back to the
  last 28 days and label the card "Spotted here this month".
* Back in town: seasonal-notify species only, whose first return
  date is on or before the page date and no more than 14 days before
  it, with a report on or before the page date.  Sorted by first
  return date descending, then species name ascending.  If none
  qualifies, the page carries the absence note instead of an empty
  card, exactly as the layout rules require.
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

SPOTTED_WINDOW_DAYS = 7
SPOTTED_FALLBACK_DAYS = 28
BACK_IN_TOWN_WINDOW_DAYS = 14
NO_SEASONAL_NOTE = (
    "No seasonal arrivals in the last 14 days. "
    "All seasonal birds on your list have settled in — "
    "nothing new to announce this week."
)


@dataclass(frozen=True)
class MonthlySpecies:
    species: str
    days: int
    habitat: str = ""
    mark_label: str = ""
    illustration: str = ""


@dataclass(frozen=True)
class SpottedCandidate:
    species: str
    obs_date: date
    place: str
    distance_mi: float
    count: int
    habitat: str = ""
    mark_label: str = ""
    illustration: str = ""


@dataclass(frozen=True)
class BackCandidate:
    species: str
    first_return: date
    report_date: date
    season: str
    hotspot: str
    habitat: str = ""
    mark_label: str = ""
    illustration: str = ""


@dataclass(frozen=True)
class BirdPage:
    page_date: date
    monthly: MonthlySpecies
    also_monthly: tuple[MonthlySpecies, ...]
    spotted: SpottedCandidate | None
    spotted_label: str
    back: BackCandidate | None
    absence_note: str | None


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def load_snapshot(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def select_page(snapshot: dict, page_date: date) -> BirdPage:
    monthly_raw = snapshot["monthly_top"]
    monthly = tuple(
        MonthlySpecies(
            species=item["species"],
            days=int(item["days"]),
            habitat=item.get("habitat", ""),
            mark_label=item.get("mark_label", ""),
            illustration=item.get("illustration", ""),
        )
        for item in monthly_raw
    )
    if not monthly:
        raise ValueError("snapshot monthly_top must not be empty")
    life_list = set(snapshot.get("life_list", []))

    week_start = page_date - timedelta(days=SPOTTED_WINDOW_DAYS - 1)
    month_start = page_date - timedelta(days=SPOTTED_FALLBACK_DAYS - 1)
    spotted_all = [
        SpottedCandidate(
            species=item["species"],
            obs_date=_parse_date(item["obs_date"]),
            place=item["place"],
            distance_mi=float(item["distance_mi"]),
            count=int(item.get("count", 1)),
            habitat=item.get("habitat", ""),
            mark_label=item.get("mark_label", ""),
            illustration=item.get("illustration", ""),
        )
        for item in snapshot.get("spotted_candidates", [])
        if item["species"] not in life_list
    ]
    week = [c for c in spotted_all if week_start <= c.obs_date <= page_date]
    month = [c for c in spotted_all if month_start <= c.obs_date <= page_date]
    if week:
        spotted = sorted(week, key=lambda c: (-c.obs_date.toordinal(), c.distance_mi, c.species))[0]
        spotted_label = "Spotted here this week"
    elif month:
        spotted = sorted(month, key=lambda c: (-c.obs_date.toordinal(), c.distance_mi, c.species))[0]
        spotted_label = "Spotted here this month"
    else:
        spotted = None
        spotted_label = "Spotted here this week"

    back_all = [
        BackCandidate(
            species=item["species"],
            first_return=_parse_date(item["first_return"]),
            report_date=_parse_date(item["report_date"]),
            season=item["season"],
            hotspot=item["hotspot"],
            habitat=item.get("habitat", ""),
            mark_label=item.get("mark_label", ""),
            illustration=item.get("illustration", ""),
        )
        for item in snapshot.get("back_candidates", [])
    ]
    back_qualifying = [
        c
        for c in back_all
        if c.first_return <= page_date
        and 0 <= (page_date - c.first_return).days <= BACK_IN_TOWN_WINDOW_DAYS
        and c.report_date <= page_date
    ]
    back = None
    if back_qualifying:
        back = sorted(
            back_qualifying,
            key=lambda c: (-c.first_return.toordinal(), c.species),
        )[0]

    return BirdPage(
        page_date=page_date,
        monthly=monthly[0],
        also_monthly=monthly[1:],
        spotted=spotted,
        spotted_label=spotted_label,
        back=back,
        absence_note=None if back is not None else NO_SEASONAL_NOTE,
    )
