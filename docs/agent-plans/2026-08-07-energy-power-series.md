# Energy Power-Series Plot Plan

## Goal

Change the sixteen 24-hour energy-dashboard line plots from cumulative energy to
average power versus time. Each plot should overlay today's and yesterday's
15-minute average kW readings, while retaining the useful Today / Yesterday daily
kWh totals in the panel header and leaving the seven-day energy/TV bar charts
unchanged.

## Constraints

- Reuse the existing Emporia `15MIN` samples and persistent cache; do not add API
  calls or force a historical data migration.
- Emporia returns kWh for each 15-minute interval, so average power is
  `interval_kwh / 0.25 hours`, equivalently `interval_kwh * 4` kW.
- Preserve the 4x4 grid, despined axes, black Today line, gray Yesterday line,
  right-side seven-day charts, exact `1872x1404` size, and true 4-bit encoding.
- Keep panel header totals in kWh because those remain daily energy totals.
- Make the plotted y-axis unit explicitly kW and the 15-minute averaging window
  clear without restoring the removed legend, boxes, or grid lines.
- Missing 15-minute buckets must not be treated as zero or bridged by a misleading
  line across a long gap.
- Existing weekly/month routes and the live server cache contract must not change.

## Discovered Context

- `EnergyState.quarter_samples` already persists raw timestamped 15-minute kWh
  buckets for mains and fourteen physical circuits.
- `dashboard_from_state()` currently collapses those samples into cumulative
  `CumulativeEnergyPoint` objects before handing them to the renderer.
- The right-side current-day mains bar independently agrees with the cumulative
  Total endpoint, confirming the underlying energy values are correct.
- The live server is deployed at `/energy/*`, uses the shared rendered-image
  fingerprint cache, and can be updated by restarting then forcing one manual
  energy regeneration.
- The current automated suite has 56 passing tests.
- Linear is not configured and was not requested.

## Alternatives Considered

### 1. Plot kWh per 15-minute bucket

This would require no unit conversion, but the y-axis would describe an arbitrary
interval quantity and would be harder to compare with normal household power
ratings. Rejected because it is not the conventional meaning of usage versus
time.

### 2. Plot instantaneous watts

Emporia minute or second samples could approximate instantaneous demand, but that
would materially increase API volume and visual noise. Rejected because the
existing 15-minute cache already provides an efficient trend signal.

### 3. Plot 15-minute average kW

Convert each interval's kWh to average kW and plot it at the interval midpoint.
This is physically meaningful, comparable across time/circuits, and requires no
new upstream requests. **Selected.**

### 4. Step plot versus connected line

A step plot is literal for interval averages but becomes visually heavy in sixteen
small panels. A direct unsmoothed line through interval midpoints retains the
requested line-plot style. **Selected**, with separate segments across data gaps.

## Final Design

- Replace the plot-point contract with a unit-neutral or power-specific point
  carrying local hour and average kW.
- Build Today and Yesterday points directly from each day's deduplicated
  `TimedEnergySample` values:
  - x = local interval midpoint (`bucket start + 7.5 minutes`);
  - y = `max(0, sample.kwh * 4)` kW.
- Split rendered lines whenever adjacent timestamps are more than one normal
  15-minute step apart, so outages do not appear as fabricated trends.
- Continue calculating `today_kwh` and `yesterday_kwh` by summing interval kWh.
- Change the grid subtitle to communicate `15-MIN AVERAGE POWER • kW` and retain
  `VALUES: TODAY / YESTERDAY kWh` for headers.
- Format small kW axes with enough precision for standby circuits; use compact
  decimals rather than rounding sub-1 kW loads to zero.
- Update deterministic mock rendering, data/renderer tests, and the tracked sample
  PNG.
- After tests and visual review, restart the service, force one energy render, and
  verify a new public fingerprinted image.

## Chunk List

### Chunk 1: Replace cumulative plots with 15-minute average power

- **Objective:** Update the normalized plot contract, renderer, tests, sample, and
  live deployment so the 4x4 grid shows kW versus time.
- **Files or areas likely to change:** `energy_data.py`, `energy_render.py`,
  `tests/test_energy_data.py`, `tests/test_energy_render.py`,
  `outputs/energy-dashboard.png`, and this plan's progress log.
- **Dependencies on other chunks:** Existing completed Emporia dashboard plan.
- **Non-goals:** New Emporia calls, changes to daily bars/TV threshold, server
  routing changes, or redesigning the page.
- **Commands to run:** `uv run render-trmnl-energy`; focused energy tests; full
  unittest discovery; `python3 -m compileall src tests`; `git diff --check`.
- **Expected checks:** interval conversion equals kWh times four, points use
  interval midpoint hours, daily kWh totals are unchanged, long gaps split line
  segments, axes/subtitle say kW, PNG remains exact 4-bit target, and all routes
  regressions pass.
- **Blocker-report format:** `Chunk 1 blocked: <issue>; evidence: <test/output>;
  attempted: <approaches>; decision needed: <specific choice>.`

## Subagent Packets

One bounded implementation packet may own the data-contract, renderer, and
focused tests because they form one atomic semantic change. The primary agent
retains final visual review, live service regeneration, and public verification.

## Acceptance Criteria

- All sixteen line plots show 15-minute average kW versus local time rather than
  cumulative kWh.
- Today remains black and partial; yesterday remains gray and complete.
- Panel header values remain clearly labeled daily kWh totals.
- The page explicitly distinguishes plot kW from header/bar kWh.
- Missing intervals are not converted to zeros or visually bridged across long
  gaps.
- Seven-day total-energy and TV-time bars are unchanged.
- Mock and live PNGs remain `1872x1404`, true 4-bit grayscale.
- The full test suite passes and the public Redirect endpoint publishes a new
  fingerprint.

## Test Plan

- Unit-test `0.25 kWh / 15 minutes = 1.0 kW`.
- Test midpoint placement for midnight and late-day buckets.
- Test daily energy totals remain sums of interval kWh.
- Test gap segmentation with an omitted bucket.
- Test independent rounded kW scales and small sub-1 kW formatting.
- Render and visually inspect deterministic and live images.
- Run the full repository test suite and compileall.
- Restart the service, force a live energy render, and verify local/public JSON,
  image dimensions, bit depth, and new fingerprint.

## Linear Links

Not configured.

## Progress Log

- 2026-08-07: Plan created from user feedback requesting usage versus time rather
  than cumulative energy.
