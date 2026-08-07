# Energy Dashboard Layout Refinement Plan

## Goal

Refine the energy dashboard hierarchy by centering larger all-caps subplot titles,
placing each panel's daily kWh totals beneath its title, compacting the main header
to one line, stretching the bar charts to the full 4x4-grid height, and displaying
light-circuit axes in watts.

## Constraints

- Preserve the 4x4 grid, month-to-date ordering, Today/Yesterday traces, detached
  spines, five y ticks, seven-day data, and 1872x1404 4-bit output.
- Keep `HOME ENERGY` left-aligned and place the descriptive/unit key right-aligned
  on the same header line.
- Keep the right-side `7-DAY OVERVIEW` identity readable without consuming chart
  height unnecessarily.
- Center both the subplot name and its Today/Yesterday kWh totals.
- Use all caps in the existing Roboto Flex family rather than introducing another
  font asset.
- Dynamically use W only for plot scales at or below 100 W; higher-load panels
  remain in kW.
- An idle panel must have a useful nonzero scale, while an active Oven or other
  normally large appliance must automatically return to kW.

## Discovered Context

- The current three-line centered header consumes 124 vertical pixels.
- Subplot names and totals currently share one left/right-aligned line in 20 px
  and 18 px fonts.
- The two bar-chart boxes currently span y=164..1239, while the plot grid spans
  y=124..1348; the footer sits inside that otherwise-unused lower chart area.
- Low-scale y labels are precise but can become decimal-heavy in kW; converting
  scales up to 0.1 kW into W yields simpler values such as 0, 25, 50, 75, 100 W.
- `rounded_axis_max()` currently assigns empty panels a 1 kW default, which makes
  an idle Oven appear heavier than requested.
- The full suite currently has 65 passing tests. Linear is not configured or
  requested.

## Alternatives Considered

### 1. Keep panel titles and totals on one line, but center the pair

This saves height but does not establish the requested centered title hierarchy
and still forces aggressive truncation. Rejected.

### 2. Center a two-line panel header

Use a 22 px semibold all-caps title and a smaller centered kWh line underneath.
Selected because it is visually clear and the compact main header recovers enough
vertical space for it.

### 3. Force named circuits such as Oven and Sump Pump to W

This becomes misleading when the Oven is active at kilowatt scale and requires
label-specific rules. Rejected.

### 4. Select W or kW from the current panel scale

Use W when the rounded maximum is at most 0.1 kW and kW otherwise, with an empty
panel default of 0.1 kW. Selected because it follows the data and remains correct
when appliance behavior changes.

### 5. Stretch only the bar plot rectangles

This leaves mismatched outer chart regions and inconsistent titles. Rejected in
favor of making the two stacked chart boxes collectively share the exact top and
bottom bounds of the 4x4 grid.

## Final Design

- Reduce `HEADER_BOTTOM` to roughly 72 px.
- Draw `HOME ENERGY` at the left grid margin and one right-aligned description at
  `GRID_RIGHT` on the same line. The description will state 15-minute average
  power, adaptive W/kW axes, and Today/Yesterday kWh panel totals.
- Keep `7-DAY OVERVIEW` compactly aligned within the summary column.
- Make each panel header two centered lines:
  - uppercase, ellipsized 22 px semibold circuit name;
  - centered 18 px Today / Yesterday kWh values beneath it.
- Move the data rectangle down within each enlarged panel to clear the two-line
  title while preserving detached spines and five y ticks.
- Set the two summary boxes from `HEADER_BOTTOM` through `CONTENT_BOTTOM`, split
  evenly with the existing gap. Move the source/freshness footer below
  `CONTENT_BOTTOM`.
- Change the empty plot maximum from 1.0 kW to 0.1 kW.
- Add an axis display helper:
  - maximum <= 0.1 kW: multiply tick values by 1,000 and label maximum `W`;
  - maximum > 0.1 kW: retain kW values and label maximum `kW`.
- Update geometry, title, unit, pixel, and output tests; regenerate and visually
  inspect mock/live images; deploy via render-cache invalidation without forcing
  historical Emporia data.

## Chunk List

### Chunk 1: Refine headers, chart geometry, and adaptive units

- **Objective:** Implement the compact page header, centered all-caps two-line
  panel headers, full-height stacked bar charts, and data-driven W/kW axes.
- **Files or areas likely to change:** `energy_render.py`,
  `tests/test_energy_render.py`, `outputs/energy-dashboard.png`, and this plan.
- **Dependencies on other chunks:** None.
- **Non-goals:** Energy-data fetching/calculation changes, plot sorting changes,
  bar-chart content changes, new font assets, or W conversion for daily totals.
- **Commands to run:** sample renderer; focused renderer tests; full unittest
  discovery; compileall; `git diff --check`; PNG validation; local/public endpoint
  checks.
- **Expected checks:** title/totals are centered and non-overlapping, uppercase
  labels fit, bar-chart boxes align with grid bounds, low scales show W, active
  high scales show kW, empty panels use 100 W, all tests pass, and live output is
  visually balanced.
- **Blocker-report format:** `Chunk 1 blocked: <issue>; evidence: <output>;
  attempted: <approaches>; decision needed: <choice>.`

## Subagent Packets

This is one tightly coupled renderer geometry pass requiring unified visual
inspection; no subagent packet is needed.

## Acceptance Criteria

- The main header is one line with left-aligned title and right-aligned description.
- Every subplot name is centered, uppercase, larger than before, and has centered
  Today/Yesterday kWh values below it.
- The two bar-chart regions collectively use the exact vertical extent of the 4x4
  panel grid, and the footer sits below both.
- Scales at or below 100 W use readable W ticks; larger scales use kW.
- An idle panel defaults to a 100 W scale, while active high-load circuits remain
  in kW.
- Detached spines, five y ticks, line styles, data, routes, and image encoding are
  preserved.
- The full test suite passes and a new public fingerprint is deployed.

## Test Plan

- Test header alignment constants and description content.
- Test panel header layout returns centered uppercase title and centered totals.
- Test long names ellipsize within the panel width.
- Test summary chart boxes begin/end at the same y bounds as the 4x4 grid.
- Test 0.08 kW and 0.1 kW maxima format ticks in W, 0.15 kW formats in kW, and an
  empty panel defaults to 0.1 kW.
- Retain detached-spine, no-grid, ordering, line, and target-PNG tests.
- Render and visually inspect mock and live output for clipping and spacing.
- Run focused/full tests, compileall, diff check, and public endpoint verification.

## Linear Links

Not configured.

## Progress Log

- 2026-08-07: Plan created from the revised subplot-title, page-header, bar-height,
  and light-circuit unit requests.
- 2026-08-07: Chunk 1 DONE. Added the compact left/right page header, centered
  22 px all-caps panel names with centered kWh totals, full-grid-height summary
  boxes, a footer below the charts, and adaptive W/kW tick formatting. All 13
  focused renderer tests and the full 67-test suite pass with compileall, diff,
  and exact 4-bit PNG checks.
- 2026-08-07: Final review removed the now-unused centered-header helper and found
  no remaining correctness, integration, regression, or unrelated-change issues.
  Mock and live images were visually inspected; live light circuits use W while
  larger circuits retain kW. Published public fingerprint `a15d0dacbae40c34`.
