# Emporia Energy Dashboard Plan

## Goal

Add a third TRMNL X Redirect plugin that presents household energy data from an
Emporia Vue as a compact comparison dashboard:

- a 4x4 grid of cumulative-energy line plots for Total, the fourteen named
  physical circuits, and Emporia Balance;
- each plot overlays yesterday's complete 24-hour curve in light gray with
  today's curve in black from midnight through the most recent sample;
- a right-side column contains seven-day bar charts for whole-home energy and
  estimated TV screen time.

The dashboard should be useful at a glance, remain readable in 4-bit grayscale,
avoid excessive calls to Emporia's unofficial cloud API, and continue serving a
last-known-good image during upstream failures.

## Constraints

- The output must remain exactly `1872x1404`, grayscale, and compatible with the
  existing true 4-bit PNG encoder.
- The Redirect JSON request must remain small and fast enough for TRMNL's
  two-second timeout. Rendering and Emporia access must happen behind the
  existing image cache, never in the JSON response path beyond cache lookup.
- Emporia does not provide a supported public or local API. The implementation
  will use the community `pyemvue` client and must isolate that dependency behind
  an internal adapter.
- Emporia cloud data and authentication material are sensitive. Credentials,
  tokens, device identifiers, and cached readings must not be committed.
- The service refreshes every 900 seconds. Routine refreshes must not refetch all
  historical series.
- Emporia currently caps a minute-resolution chart request at 13 hours 20
  minutes. TV history backfill must use chunks no longer than 12 hours.
- Full-history `15MIN` chart requests work for the yesterday-plus-today window,
  but Emporia Balance cannot be fetched as a chart series.
- The Vue reports fourteen named physical circuits. Balance is derived and is the
  fifteenth non-total plot.
- Local-day boundaries must use the Vue's `America/Denver` timezone and remain
  correct across daylight-saving transitions.
- The secrets file at `/home/ubuntu/.secrets` currently contains
  `EMPORIA_USERNAME` and `EMPORIA_PASSWORD`; its values must never be logged.
  Deployment should change its mode from `664` to `600`.
- The persistent systemd service currently reads `/home/ubuntu/.hermes/.env`, not
  `/home/ubuntu/.secrets`; deployment must explicitly make the Emporia variables
  available to the unit without copying them into the repository.

## Discovered Context

- `server.py` already models weekly and month views as `CalendarPlugin` objects,
  persists rendered PNGs, returns stale images while refreshing in the
  background, and fingerprints image bytes for TRMNL.
- Routes are currently allowlisted for `weekly` and `month`; adding `energy`
  requires extending both `plugin_specs()` and `resolve_route()`.
- Renderers use Pillow and share the `1872x1404` canvas and vendored fonts from
  `render.py`.
- Tests use `unittest`, `unittest.mock`, temporary directories, and direct image
  assertions. There is no external-service integration test suite.
- `outputs/server-cache/` is ignored by Git and is the existing location for
  private persisted render state.
- External API logging already strips URL query strings, which prevents Emporia
  device/channel identifiers from appearing in normal request logs. Emporia
  calls should use the same logging conventions.
- A read-only live probe found one mains series, fourteen named physical circuit
  series, and a calculated Balance series. The individual circuit values plus
  Balance reconcile with mains usage.
- A `15MIN` chart request for the combined yesterday/today window succeeds.
  Historical chart requests for mains and physical channels succeed; a chart
  request for Balance returns HTTP 400, so Balance must be derived bucket by
  bucket as `max(0, mains - sum(physical circuits))`.
- TV minute samples are clearly bimodal: standby clusters around 7 W, while
  active use is generally 80-136 W. A 40 W threshold cleanly separates the two
  modes and produced plausible daily runtimes of roughly 3-6.25 hours in the
  sampled week.
- `get_device_list_usage` can return all circuit values for one scale/instant in
  one request. This is preferred for routine 15-minute updates after initial
  chart backfill.
- Linear is not configured (`LINEAR_API_KEY` is absent), and the user did not
  request Linear tracking.

## Alternatives Considered

### 1. Instantaneous-power lines versus cumulative-energy lines

- Instantaneous power would reveal appliance cycling but would be visually noisy,
  make yesterday/today comparison harder in very small plots, and conflict with
  the request to show energy consumed.
- Cumulative kWh starts every plot at zero at local midnight and answers whether
  today's consumption is ahead of or behind yesterday at the same hour.
- **Decision:** use cumulative kWh formed by summing 15-minute energy buckets.
  Yesterday continues through 24:00; today stops at the latest available bucket.

### 2. One overlaid chart or a 4x4 small-multiple grid

- One chart with sixteen lines would conserve space but be unreadable in 4-bit
  grayscale and would bury low-energy circuits under HVAC loads.
- A 4x4 grid gives every series a stable location and permits an independent
  zero-based y-scale, keeping low-usage devices visible.
- **Decision:** use the requested 4x4 small-multiple grid. Display the numeric
  daily total and y-maximum in each panel so independent scales are explicit.

### 3. Full historical fetch on every render versus incremental persistence

- Refetching fifteen chart series plus TV minute chunks every 15 minutes is easy
  to implement but wasteful and more likely to trigger cloud throttling.
- A persistent sample cache requires rollover, gap, and deduplication logic but
  reduces the steady-state work to an all-channel 15-minute update, one small TV
  minute update, and occasional daily-total finalization.
- **Decision:** backfill once, then persist and update incrementally. Force refresh
  may refetch the current window but must still respect request-size limits.

### 4. Direct Balance history versus derived Balance

- Emporia returns Balance in daily usage responses, but the historical chart
  endpoint rejects the synthetic Balance channel.
- **Decision:** derive each 15-minute Balance bucket from mains less the fourteen
  physical circuits, clamped at zero for display. Retain the unclamped residual in
  diagnostic logs/tests so sensor configuration errors are detectable.

### 5. Five versus seven bars

- Five bars allow larger labels, but seven provide a familiar week-long rhythm
  and fit in the right-side column at the target resolution.
- **Decision:** render seven local calendar days, including today. Today is marked
  as partial and uses a stronger tone.

## Final Design

### Data model and time semantics

Add `energy_data.py` with immutable public data objects along these lines:

- `EnergyDashboardData`: timezone, seven display dates, today/yesterday dates,
  sixteen plot series, daily mains totals, daily TV runtime, freshness/source,
  and completeness flags.
- `EnergyPlotSeries`: stable key, display label, channel number or synthetic kind,
  yesterday cumulative points, today cumulative points, and daily totals.
- `TimedEnergySample`: UTC bucket start and kWh, stored internally so local-day
  regrouping remains DST-safe.

Use local calendar dates for presentation but UTC instants for storage and
deduplication. A 23-hour or 25-hour DST day still maps correctly onto a normalized
0-24 local-hour x-axis. Data gaps are excluded rather than treated as zero and are
reported through completeness flags/logging.

### Emporia adapter and authentication

- Add a narrow `EmporiaClient` protocol/adapter around `pyemvue` so tests never
  contact the network.
- Add a compatible bounded `pyemvue` dependency to `pyproject.toml` and refresh
  `uv.lock` during implementation.
- Read `EMPORIA_USERNAME` and `EMPORIA_PASSWORD` only from the environment.
- Store refresh tokens outside the repository at `EMPORIA_TOKEN_FILE`, defaulting
  under `XDG_STATE_HOME` or `~/.local/state/trmnl-calendar/emporia-tokens.json`.
  Create parent directories privately and enforce file mode `600`.
- Never include raw exception URLs, credentials, device IDs, or channel IDs in
  logs. Log provider, operation, status, duration, sample count, and a stable
  non-reversible configuration fingerprint.

### Persistent energy sample cache

Add a lock-protected cache with atomic JSON persistence, configurable through
`TRMNL_ENERGY_STATE_PATH` and defaulting inside `outputs/server-cache/`.

- On first load or after a gap, fetch mains plus fourteen physical channels from
  the chart endpoint at `15MIN` resolution for yesterday and today. Reconstruct
  timestamps from `firstUsageInstant` and deduplicate inclusive endpoints.
- In steady state, use one all-channel `get_device_list_usage` request for the
  current/recent 15-minute bucket and replace partial bucket values when a later
  reading arrives.
- Retain at least three local days of 15-minute samples, which covers yesterday,
  today, rollover, and DST edges.
- Backfill TV at `1MIN` resolution in chunks no longer than 12 hours. Persist raw
  minute samples for eight local days so threshold changes can be recalculated.
- In steady state, request only the missing/recent TV minutes and overwrite the
  current partial minute if needed.
- Obtain seven daily mains totals from cached 15-minute data when complete;
  otherwise use a bounded daily chart request and persist finalized totals.
- Prune old readings after each successful persistence. Corrupt cache files are
  quarantined/ignored without exposing their contents.
- On upstream failure, use the last complete cached data. If credentials are not
  configured, local/manual rendering uses deterministic mock data. If credentials
  are configured but no real cache exists, render a clearly labeled unavailable
  state rather than presenting mock values as real.

### TV runtime

- Default `EMPORIA_TV_CHANNEL_NAME=TV` and
  `EMPORIA_TV_ON_THRESHOLD_WATTS=40`, both configurable.
- Convert each one-minute kWh sample to average watts with
  `watts = minute_kwh * 60_000`.
- Count each unique minute at or above the threshold as one on-minute; daily screen
  time is on-minutes divided by 60.
- Do not interpolate across missing minutes. Track sample coverage and mark a day
  incomplete when materially short of its expected duration.
- Keep the initial implementation threshold-based; session detection, hysteresis,
  and standby subtraction are non-goals unless real data later shows threshold
  chatter. The observed 7 W/80+ W separation does not currently require them.

### Renderer

Add `energy_render.py` using Pillow and the existing font/canvas helpers.

- Reserve roughly 74% of the usable width for a 4x4 grid and 26% for the right
  summary column, separated by a clear gutter.
- Panel order is stable across refreshes: Total first, physical circuits in
  channel-number order, and Balance last. Labels are normalized for display only
  (for example, correcting known spelling errors) without changing Emporia.
- Each small plot has a zero baseline and its own rounded y-maximum. Yesterday is
  a medium-light solid gray line; today is a thicker black line with a black end
  marker. Sparse six-hour vertical guides establish the shared 0-24 hour axis.
- Each panel shows a compact name plus today's and yesterday's cumulative kWh.
  The Total panel receives a heavier frame but uses the same plotting grammar.
- The right column contains two vertically stacked seven-bar charts:
  1. whole-home kWh per day;
  2. TV hours on per day.
- Both bar charts use a shared day-label treatment, print compact values, and make
  today's partial bar visually distinct without relying on color.
- Include a small legend for Today/Yesterday and a freshness/partial-data note.
- Provide deterministic mock data and a CLI entry point that writes
  `outputs/energy-dashboard.png` for visual inspection.

### Server and routes

- Register an `energy` plugin with filename prefix `energy-dashboard`.
- Add `/energy/trmnl.json`, `/energy/image.png`, and
  `/energy/calendar.png` routes. Keep existing root, weekly, and month behavior
  unchanged.
- Use the existing rendered-image persistence, fingerprint, stale-while-refresh,
  and 900-second refresh cadence.
- Update server startup output and repository documentation with the third public
  Redirect endpoint.

### Deployment

- Change `/home/ubuntu/.secrets` to mode `600`.
- Add `/home/ubuntu/.secrets` as a systemd `EnvironmentFile` or migrate the two
  Emporia variables into the already-loaded private environment file. Prefer the
  explicit second `EnvironmentFile` so credential ownership remains clear.
- Set an explicit persistent `EMPORIA_TOKEN_FILE` outside the repository.
- Run `uv sync`, reload/restart the service as appropriate, and verify all three
  plugins plus `/healthz` locally before checking the public endpoint.

## Chunk List

### Chunk 1: Energy contracts, Emporia adapter, and persistent cache

- **Objective:** Implement authenticated read-only Emporia loading, 15-minute and
  TV-minute backfill, incremental updates, Balance derivation, persistence, mock
  data, and normalized dashboard data objects.
- **Files or areas likely to change:** `pyproject.toml`, `uv.lock`, new
  `src/trmnl_weekly_calendar/energy_data.py`, new `tests/test_energy_data.py`, and
  possibly `external_api_log.py` for a generic sanitized operation logger.
- **Dependencies on other chunks:** None.
- **Non-goals:** Rendering, HTTP routes, service changes, or writing credentials.
- **Commands to run:** `uv sync`; `uv run python -m unittest
  tests.test_energy_data`; `python3 -m compileall src tests`.
- **Expected checks:** Timezone/DST grouping, 12-hour TV chunking, timestamp
  deduplication, incremental bucket replacement, 40 W runtime calculation,
  Balance derivation, cache persistence/pruning/corruption handling, safe logging,
  and mock/unavailable behavior all pass with fake clients.
- **Blocker-report format:** `Chunk 1 blocked: <failing contract>; evidence:
  <test/error>; attempted: <approaches>; decision needed: <specific choice>.`

### Chunk 2: 4x4 comparison renderer and right-side bar charts

- **Objective:** Render the final `1872x1404` dashboard from normalized/mock data.
- **Files or areas likely to change:** new
  `src/trmnl_weekly_calendar/energy_render.py`, font definitions/helpers only where
  reuse is justified, new `tests/test_energy_render.py`, `pyproject.toml` CLI entry,
  and `outputs/energy-dashboard.png` as a manually generated artifact if outputs
  are intentionally tracked.
- **Dependencies on other chunks:** Chunk 1 data contracts and mock fixture.
- **Non-goals:** Live authentication, HTTP routing, or changing existing calendar
  layouts.
- **Commands to run:** `uv run render-trmnl-energy`; `uv run python -m unittest
  tests.test_energy_render`; `python3 -m compileall src tests`.
- **Expected checks:** Exact dimensions/mode, sixteen stable panels, zero-based
  cumulative overlays, today truncation, independent scaling, seven energy bars,
  seven TV-hour bars, legible labels, and current-day partial treatment.
- **Blocker-report format:** `Chunk 2 blocked: <layout/data mismatch>; evidence:
  <image/test>; attempted: <variants>; decision needed: <specific choice>.`

### Chunk 3: Server integration and route verification

- **Objective:** Expose the renderer through the third Redirect plugin without
  regressing weekly/month behavior.
- **Files or areas likely to change:** `server.py`, `tests/test_server.py`, and
  endpoint documentation such as `README.md` or repository operating notes.
- **Dependencies on other chunks:** Chunks 1 and 2.
- **Non-goals:** Editing systemd or real credentials.
- **Commands to run:** `uv run python -m unittest discover -s tests`; `python3 -m
  compileall src tests`; start the server and curl `/healthz`, existing weekly and
  month endpoints, `/energy/trmnl.json`, and `/energy/image.png`.
- **Expected checks:** Route resolution, JSON filename/URL/fingerprint, PNG headers
  and integer content length, stale-while-refresh behavior, forced refresh, and no
  changes to legacy aliases.
- **Blocker-report format:** `Chunk 3 blocked: <route/cache failure>; evidence:
  <response/test>; attempted: <approaches>; decision needed: <specific choice>.`

### Chunk 4: Secure deployment and live validation

- **Objective:** Securely expose Emporia credentials to the service, initialize
  persistent tokens/state, restart, and validate the live energy screen.
- **Files or areas likely to change:** `/home/ubuntu/.secrets`,
  `/etc/systemd/system/trmnl-calendar.service`, external token/state paths, and
  plan progress notes. Do not commit secret-bearing files.
- **Dependencies on other chunks:** Chunks 1-3 complete and tests passing.
- **Non-goals:** Changing Emporia device configuration or publishing credentials.
- **Commands to run:** `chmod 600 /home/ubuntu/.secrets`; `uv sync`; `sudo
  systemctl daemon-reload`; `sudo systemctl restart trmnl-calendar.service`;
  `systemctl status trmnl-calendar.service --no-pager`; `journalctl -u
  trmnl-calendar.service -n 100 --no-pager`; curl local health and all plugin
  endpoints, then the public energy Redirect endpoint.
- **Expected checks:** Unit runs as `ubuntu`, secrets/tokens are private, startup
  backfill completes without leaking identifiers, energy JSON stays fast, PNG is
  valid 4-bit grayscale, and subsequent refreshes are incremental.
- **Blocker-report format:** `Chunk 4 blocked: <deployment/live-data issue>;
  evidence: <sanitized status>; attempted: <safe actions>; decision needed:
  <specific choice>.`

## Subagent Packets

No subagents were used during planning. If implementation is later delegated,
use these bounded packets after the plan commit:

1. **Data packet:** Execute Chunk 1 only. Own energy data/cache files and tests;
   do not touch renderer or server routes. Return contracts plus passing commands.
2. **Renderer packet:** After Chunk 1 contracts exist, execute Chunk 2 only using
   mock data. Own renderer and renderer tests; do not perform live Emporia calls.
3. **Integration packet:** After Chunks 1-2, execute Chunk 3 and perform a focused
   review of route/cache regressions. Do not mutate systemd or secrets.
4. **Deployment packet:** Keep on the primary agent because it touches private
   local configuration and service state. Execute only after code review/tests.

## Acceptance Criteria

- `/energy/trmnl.json` returns the standard Redirect JSON shape with a stable
  fingerprint when image bytes are unchanged and points to `/energy/image.png`.
- `/energy/image.png` is an `1872x1404`, 16-level grayscale PNG with correct
  `Content-Type` and integer `Content-Length`.
- The screen contains exactly sixteen small plots in a 4x4 grid: Total, fourteen
  physical circuits, and derived Balance.
- Every small plot starts at zero at local midnight, overlays the complete
  yesterday curve in light gray, and draws today in black only through the latest
  sample.
- Each plot's scaling is zero-based and independently rounded; numeric labels make
  different scales apparent.
- The right column shows seven daily whole-home kWh bars and seven TV-runtime bars,
  including a clearly partial today bar.
- TV runtime counts unique one-minute samples at or above a configurable 40 W
  default and does not count missing minutes.
- Balance equals non-negative mains minus the fourteen physical circuit values for
  each aligned 15-minute bucket.
- Initial backfill respects Emporia's minute-query limit, and steady-state refresh
  does not refetch full historical series.
- Cached real data and the last rendered image remain usable through a temporary
  Emporia failure; mock data is never silently shown as live data.
- Credentials, tokens, account names, device IDs, and raw private cache files do
  not enter Git or logs.
- Existing weekly, month, legacy routes, tests, and 4-bit encoding continue to
  work.

## Test Plan

### Automated

- Unit-test credentials/config parsing without asserting secret values.
- Fake the Emporia adapter for device merging, stable channel identity, daily and
  15-minute normalization, and upstream error behavior.
- Test chart reconstruction from `firstUsageInstant`, inclusive endpoint
  deduplication, partial-bucket replacement, and cache rollover.
- Test Denver local-day boundaries across normal, 23-hour, and 25-hour days.
- Test minute backfill splits ranges into at most 12-hour requests.
- Test the observed TV separation around the 40 W threshold, missing samples, and
  exact per-day hours.
- Test Balance derivation with normal residuals, slight negative noise, missing
  channels, and large negative residual diagnostics.
- Test renderer dimensions, grayscale mode, panel count/order, line tones,
  truncation of today, bar count, and incomplete-data markers.
- Extend server tests for energy routes, fingerprints, headers, cache reuse, and
  unchanged legacy resolution.
- Run the entire suite with `uv run python -m unittest discover -s tests` and
  `python3 -m compileall src tests`.

### Manual/local

- Render deterministic mock data and inspect the 4x4 labels and lines at native
  resolution and a device-like downscaled preview.
- Render live data and compare selected cumulative endpoints with Emporia's daily
  values.
- Compare TV hours for at least one day against the minute-derived probe and a
  known viewing interval.
- Validate true 4-bit grayscale encoding and exact dimensions.
- Start the server and verify `/healthz`, weekly/month/legacy endpoints, and all
  energy endpoints with both GET and HEAD where applicable.
- Force one local energy refresh, then verify a normal request reuses cached bytes
  and does not trigger a historical backfill.
- Temporarily simulate an Emporia failure using a fake/mocked adapter and confirm
  the last-known-good image remains available.

## Linear Links

Not configured. `LINEAR_API_KEY` is absent, no native Linear task was requested,
and no issue was created.

## Progress Log

- 2026-08-07: Confirmed read-only Emporia authentication and enumerated one mains,
  fourteen physical circuits, and Balance without storing credentials or tokens.
- 2026-08-07: Measured TV standby/active separation and selected a configurable
  40 W default threshold.
- 2026-08-07: Confirmed the 13h20m minute-query cap and successful 12-hour chunked
  backfill over seven local days.
- 2026-08-07: Confirmed combined yesterday/today `15MIN` chart data works for a
  physical channel and mains; confirmed Balance chart history fails and must be
  derived.
- 2026-08-07: Updated the proposed layout from a 3x5 seven-day circuit grid to the
  requested 4x4 today-versus-yesterday cumulative comparison plus right-side
  seven-day bars.
- 2026-08-07: Plan written; implementation not started.
- 2026-08-07: Chunk 1 completed with normalized dashboard contracts, the
  read-only PyEmVue adapter, persistent sample caching, Balance derivation, TV
  runtime calculation, deterministic mock data, and focused tests. Status:
  `DONE_WITH_CONCERNS` because the adapter is signature-checked and unit-tested
  but intentionally has not yet been exercised against the live account.
