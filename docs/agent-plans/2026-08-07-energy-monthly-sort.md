# Energy Monthly-Usage Sort Plan

## Goal

Order the sixteen energy-dashboard plots by calendar-month-to-date energy usage:
pin Total first, then sort all physical circuits and the derived Balance from
highest to lowest kWh.

## Constraints

- Interpret "monthly" as the current local calendar month in the configured
  Emporia timezone, not a rolling 30-day window.
- Keep Total first even if provider inconsistencies make another computed value
  appear larger.
- Include today's partial usage in the ranking so the order remains useful on the
  first day of a month.
- Preserve all plot styling, Today/Yesterday lines and headers, seven-day summary
  charts, TV threshold behavior, image dimensions, and server routes.
- Avoid retaining a month of 15-minute readings or performing historical calls on
  every render.
- Existing state files must remain readable; new persisted fields are additive.

## Discovered Context

- The persistent state intentionally prunes 15-minute circuit samples to the
  current and previous local day, so it cannot calculate a true monthly ranking.
- Only mains currently has persisted daily history, and that history is pruned to
  the seven-day summary window.
- The Emporia adapter supports daily (`1D`) kWh buckets for any discovered
  channel.
- `stable_plot_series()` currently pins Total first and Balance last while
  retaining hardware channel order.
- Balance is derived per 15-minute bucket as mains minus all measured physical
  circuits; monthly Balance should use the same residual principle.
- The suite currently has 59 passing tests. Linear is not configured or requested.

## Alternatives Considered

### 1. Sort using Today plus Yesterday

This needs no additional provider data, but it is a two-day ranking rather than
the requested monthly ranking and can overreact to unusual short-term loads.
Rejected.

### 2. Retain and backfill a month of 15-minute samples

This produces the total but expands API traffic and persistent state by roughly
fifteen circuits times 2,880 buckets per 30-day month. Rejected because daily
buckets provide the same ordering signal much more efficiently.

### 3. Cache per-channel daily kWh for the current month

Fetch completed daily buckets once, fill only newly completed dates on later
refreshes, and add today's existing 15-minute sum at dashboard-build time. Derive
monthly Balance from Total minus physical circuits. Selected because it is exact
for the requested calendar-month ranking, compact, and incrementally refreshable.

## Final Design

- Add an optional per-channel daily-history mapping to `EnergyState` and its JSON
  serialization without changing the current state version, preserving backward
  compatibility with existing cache files.
- During refresh, request `1D` values for missing completed dates from the first of
  the current local month through yesterday for every physical and mains channel.
  Reuse cached dates and continue using the existing mains weekly history for the
  right-side chart.
- Prune per-channel daily history outside the current local month.
- Add `month_kwh` to each `EnergyPlotSeries`:
  - physical and Total = cached completed current-month days plus today's partial
    15-minute buckets;
  - Balance = `max(0, Total month kWh - sum(physical month kWh))`.
- Update `stable_plot_series()` to pin Total first, then sort every remaining
  series descending by `month_kwh`, with normalized label/key tie-breakers for
  deterministic output.
- Keep the displayed panel header values as Today / Yesterday kWh; the monthly
  value is used only for ordering.
- Update deterministic mock data, focused tests, and the tracked sample image.
- Restart the service, force a refresh to populate monthly history, and verify the
  live and public image ordering.

## Chunk List

### Chunk 1: Persist and calculate current-month per-circuit usage

- **Objective:** Add backward-compatible daily channel history, incremental
  current-month refresh, pruning, and `month_kwh` calculations including Balance.
- **Files or areas likely to change:** `energy_data.py`, `tests/test_energy_data.py`.
- **Dependencies on other chunks:** None.
- **Non-goals:** Renderer layout changes, rolling 30-day totals, or retaining old
  month history.
- **Commands to run:** Focused energy-data tests and `python3 -m compileall src tests`.
- **Expected checks:** old state JSON still loads, daily calls only fill missing
  completed days, today is included from quarter samples, month boundaries use the
  configured timezone, and Balance is clamped residual usage.
- **Blocker-report format:** `Chunk 1 blocked: <issue>; evidence: <test/output>;
  attempted: <approaches>; decision needed: <specific choice>.`

### Chunk 2: Sort panels and deploy the regenerated dashboard

- **Objective:** Sort Total then all other panels by descending `month_kwh`, update
  renderer tests/sample, and publish the new live image.
- **Files or areas likely to change:** `energy_render.py`,
  `tests/test_energy_render.py`, `outputs/energy-dashboard.png`, and this plan.
- **Dependencies on other chunks:** Chunk 1's `month_kwh` contract.
- **Non-goals:** Showing monthly values in the UI or changing the summary bars.
- **Commands to run:** `uv run render-trmnl-energy`; focused renderer tests; full
  unittest discovery; compileall; `git diff --check`; local and public endpoint
  checks.
- **Expected checks:** Total is first, remaining plots descend by month kWh with
  deterministic ties, PNG remains 1872x1404 true 4-bit grayscale, all tests pass,
  and the public fingerprint changes.
- **Blocker-report format:** `Chunk 2 blocked: <issue>; evidence: <test/output>;
  attempted: <approaches>; decision needed: <specific choice>.`

## Subagent Packets

The two chunks are tightly coupled through the new plot-series field and are small
enough for sequential implementation by the primary agent. No subagent packet is
needed.

## Acceptance Criteria

- Total is always the first plot.
- Every physical circuit and Balance follows in descending calendar-month-to-date
  kWh order.
- Rankings include cached completed month days and today's partial energy.
- A prior-version state file loads without migration or quarantine.
- Daily provider history is cached and not refetched on every render.
- Today/Yesterday traces and totals, seven-day bars, and TV runtime are unchanged.
- The full test suite passes and the public Redirect endpoint serves a newly
  fingerprinted valid 4-bit image.

## Test Plan

- Round-trip the additive daily-channel state field and load JSON without it.
- Verify refresh requests current-month daily data for all channels when absent
  and skips the calls once completed dates are cached.
- Verify `month_kwh` includes cached completed days plus today's quarter samples.
- Verify derived Balance monthly energy is residual and clamped at zero.
- Verify Total remains first and all remaining series sort descending with stable
  tie behavior.
- Render and inspect deterministic and live images.
- Run focused tests, full unittest discovery, compileall, diff check, and PNG
  dimension/bit-depth checks.

## Linear Links

Not configured.

## Progress Log

- 2026-08-07: Plan created from the request to order plots by total monthly usage.
- 2026-08-07: Chunk 1 DONE. Added backward-compatible per-channel daily history,
  incremental current-month `1D` refreshes, month-boundary pruning, month-to-date
  totals including today's partial buckets, and residual Balance calculation.
  All 18 focused energy-data tests, compileall, and diff checks pass.
