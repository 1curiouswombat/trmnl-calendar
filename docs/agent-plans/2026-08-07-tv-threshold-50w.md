# TV Screen-Time Threshold 50 W Plan

## Goal

Change TV screen-time classification from a 40 W threshold to 50 W.

## Constraints

- Preserve the existing rule: each unique one-minute sample at or above the
  threshold counts as one on-minute.
- Keep the existing `EMPORIA_TV_ON_THRESHOLD_WATTS` environment override.
- Do not refetch minute history solely because the threshold changes; recompute
  from the cached eight-day minute samples.
- Do not change dashboard layout, routes, or refresh cadence.

## Discovered Context

- Production has no threshold environment override and currently uses
  `DEFAULT_TV_THRESHOLD_WATTS = 40.0`.
- Screen time is calculated at dashboard-build time from persisted minute samples,
  so a render after restart will apply the new threshold to the existing history.
- The environment parser already validates numeric and non-negative overrides.
- Linear is not configured or requested.

## Alternatives Considered

### 1. Add a production-only environment override

This is operationally quick but leaves local and production defaults inconsistent.
Rejected.

### 2. Change the repository default to 50 W

This keeps every environment consistent while retaining the existing override for
future tuning. Selected.

## Final Design

- Change `DEFAULT_TV_THRESHOLD_WATTS` from `40.0` to `50.0`.
- Add coverage that the configured default is 50 W and that an explicit override
  still wins.
- Run focused and full tests, restart the service, force a live render, and verify
  the public fingerprint changes.

## Chunk List

### Chunk 1: Update, test, and deploy the 50 W threshold

- **Objective:** Set the default threshold to 50 W and publish recomputed screen
  time.
- **Files or areas likely to change:** `energy_data.py`,
  `tests/test_energy_data.py`, tracked sample/plan progress if output changes.
- **Dependencies on other chunks:** None.
- **Non-goals:** Hysteresis, session detection, TV circuit selection, or UI changes.
- **Commands to run:** focused tests, full unittest discovery, compileall,
  `git diff --check`, service restart, and public endpoint checks.
- **Expected checks:** default is 50 W, override remains supported, cached minute
  history is reclassified, all tests pass, and the deployed image is valid.
- **Blocker-report format:** `Chunk 1 blocked: <issue>; evidence: <output>;
  attempted: <approaches>; decision needed: <choice>.`

## Subagent Packets

This is one small atomic configuration change; no subagent packet is needed.

## Acceptance Criteria

- Samples below 50 W do not count as TV screen time.
- Samples at or above 50 W do count.
- `EMPORIA_TV_ON_THRESHOLD_WATTS` can still override the default.
- The live energy dashboard is regenerated successfully and all tests pass.

## Test Plan

- Test default and overridden configuration values.
- Retain the existing boundary/deduplication runtime test.
- Run the complete suite, syntax checks, and PNG validation.
- Restart and verify local/public energy endpoints.

## Linear Links

Not configured.

## Progress Log

- 2026-08-07: Plan created for the requested 50 W TV threshold.
