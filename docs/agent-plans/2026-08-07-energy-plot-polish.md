# Energy Plot Polish Plan

## Goal

Improve the 4x4 energy plot grid's visual hierarchy and axis readability by
centering its header, separating the daily-kWh key from the power subtitle,
detaching the axes in a Seaborn-like style, and adding intermediate y ticks.

## Constraints

- Preserve the 1872x1404 4-bit grayscale target, 4x4 grid, monthly ordering,
  Today/Yesterday line styling, and right-side seven-day charts.
- Center the page title over the left plot grid, not across the independent
  seven-day overview column.
- Do not add plot boxes, grid lines, or a Today/Yesterday line legend.
- Keep daily panel totals in kWh and plotted interval power in kW unambiguous.
- Maintain enough plot height and label clearance in all sixteen compact panels.

## Discovered Context

- The header currently uses one left-aligned title and one combined subtitle at
  y=27 and y=76; panels begin at y=102.
- Each panel draws left and bottom spines that meet at the data origin.
- Only zero and the rounded maximum are labeled on the y axis.
- `rounded_axis_max()` already chooses a maximum based on four readable steps, so
  quarter-step y ticks align naturally with the existing scale design.
- The full suite currently has 64 passing tests. Linear is not configured or
  requested.

## Alternatives Considered

### 1. Center the title across the full 1872-pixel canvas

This visually competes with the separate `7-DAY OVERVIEW` heading and makes the
title feel disconnected from the plot grid. Rejected.

### 2. Center a three-line header over the plot grid

Center `HOME ENERGY`, the 15-minute kW description, and a lower daily-total
Today/Yesterday key over the left grid. Selected because it creates clear scope
and unit hierarchy while leaving the overview column independent.

### 3. Add horizontal grid lines for new y ticks

This improves traceability but would add substantial visual noise across sixteen
panels and conflict with the requested Seaborn despine style. Rejected.

### 4. Detached spines with tick marks only

Offset the left and bottom spines six pixels outside the data rectangle, keep them
geometrically separated, and add five labeled y ticks without grid lines.
Selected.

## Final Design

- Increase the left-grid header depth and center these three lines over
  `CANVAS_MARGIN..GRID_RIGHT`:
  - `HOME ENERGY`;
  - `15-MIN AVERAGE POWER • kW`;
  - `PANEL TOTALS: TODAY / YESTERDAY • kWh`.
- Move the first panel row down enough to give the daily-total key breathing room,
  redistributing the fixed grid height across all four rows.
- Keep the existing data rectangle for point mapping, but draw:
  - the left spine at `data_left - 6px`, ending at `data_bottom`;
  - the bottom spine at `data_bottom + 6px`, starting at `data_left`;
  so the two spines do not touch.
- Attach x ticks to the lowered bottom spine and y ticks to the offset left spine.
- Draw five y labels at 0%, 25%, 50%, 75%, and 100% of the rounded maximum;
  append `kW` only to the maximum label to reduce repetition.
- Add geometry/tick helper tests, update affected visual assertions, regenerate the
  sample, visually inspect mock and live images, then deploy.

## Chunk List

### Chunk 1: Refine header and small-multiple axes

- **Objective:** Implement the centered three-line header, detached spines, and
  five y ticks across all panels, then deploy the polished image.
- **Files or areas likely to change:** `energy_render.py`,
  `tests/test_energy_render.py`, `outputs/energy-dashboard.png`, and this plan.
- **Dependencies on other chunks:** None.
- **Non-goals:** Data changes, chart sorting changes, right-side chart redesign,
  new legends, or grid lines.
- **Commands to run:** sample renderer; focused renderer tests; full unittest
  discovery; compileall; `git diff --check`; PNG validation; local/public endpoint
  checks.
- **Expected checks:** header lines are centered over the grid and non-overlapping,
  detached spines do not intersect, five correct y tick values render, panel text
  remains legible, the sample/live images look balanced, and all tests pass.
- **Blocker-report format:** `Chunk 1 blocked: <issue>; evidence: <output>;
  attempted: <approaches>; decision needed: <choice>.`

## Subagent Packets

This is one cohesive renderer-only chunk with tight visual iteration; no subagent
packet is needed.

## Acceptance Criteria

- `HOME ENERGY` is centered over the 4x4 plot region.
- The daily-total Today/Yesterday kWh key sits on its own lower line beneath the
  title and power subtitle.
- Left and bottom spines are visibly detached from both the data rectangle and
  each other in all sixteen panels.
- Each y axis has five labeled ticks including zero and the maximum, with no grid
  lines.
- Existing data, ordering, line semantics, bars, routes, and image encoding remain
  unchanged.
- The full suite passes and a new public fingerprint is deployed.

## Test Plan

- Test header center and separated subtitle/key constants.
- Test detached spine geometry cannot intersect.
- Test y tick values include five evenly spaced values and preserve compact
  sub-1-kW formatting.
- Update pixel-level despine assertions for the offset geometry.
- Render and visually inspect mock and live images for clipping and density.
- Run focused/full tests, compileall, diff check, and exact PNG format checks.
- Restart, regenerate without unnecessary historical refetch, and verify public
  Redirect JSON and fingerprinted PNG.

## Linear Links

Not configured.

## Progress Log

- 2026-08-07: Plan created from the requested header and axis cleanup.
