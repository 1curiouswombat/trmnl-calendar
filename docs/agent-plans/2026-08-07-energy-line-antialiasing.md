# Energy Plot Line Antialiasing Plan

## Goal

Smooth the Today/Yesterday energy traces and current-value marker without
softening the dashboard's text, ticks, spines, or bar charts.

## Constraints

- Preserve trace positions, widths, colors, gap segmentation, endpoint marker,
  axes, titles, layout, and live data semantics.
- Preserve the exact 1872x1404 true 4-bit grayscale PNG contract.
- Antialias only inside each data rectangle so detached axes and labels remain
  crisp at native resolution.
- Keep rendering fast enough for the existing 15-minute server refresh.

## Discovered Context

- Pillow currently draws both traces directly onto the final-resolution grayscale
  canvas, producing hard one-pixel stair steps on diagonals.
- Each panel's data rectangle contains no grid lines or other content, so it can be
  safely rendered as a separate white-backed patch before being pasted into place.
- Final output is quantized to sixteen grayscale levels; supersampling must create
  sufficiently stable edge coverage to survive that quantization.
- The full suite currently has 67 passing tests. Linear is not configured or
  requested.

## Alternatives Considered

### 1. Supersample the entire dashboard

This would smooth everything, but requires scaling every coordinate and font,
costs substantially more memory, and risks making text and axes softer. Rejected.

### 2. Apply a blur to the finished traces

Blur reduces jaggedness but also broadens strokes and endpoint markers, decreasing
precision. Rejected.

### 3. Supersample each trace-only plot patch

Draw Yesterday, Today, and the endpoint marker at 3x resolution, downsample with
Lanczos, and paste the result into the original data rectangle. Selected because
it produces coverage-based antialiasing while isolating the effect to plot marks.

## Final Design

- Add a `LINE_SUPERSAMPLE = 3` renderer constant.
- Render each panel's line layer on a local grayscale image whose width and height
  are three times the data rectangle.
- Scale trace widths and endpoint-marker radius by the same factor.
- Preserve the draw order: Yesterday gray first, Today black second, black endpoint
  marker last.
- Downsample with Pillow's Lanczos resampler to the native plot rectangle and paste
  it before returning from the panel renderer.
- Keep axes/ticks/text on the existing native-resolution canvas and outside the
  replaced data rectangle.
- Add tests that the supersampled layer has intermediate gray edge pixels, retains
  black/gray core pixels, preserves dimensions, and does not bridge missing data.
- Regenerate/inspect mock and live images, run all checks, and deploy via render
  cache invalidation without a historical Emporia refetch.

## Chunk List

### Chunk 1: Supersample and deploy energy traces

- **Objective:** Implement isolated 3x supersampling for the plot traces and
  endpoint markers, verify 4-bit output quality, and deploy.
- **Files or areas likely to change:** `energy_render.py`,
  `tests/test_energy_render.py`, `outputs/energy-dashboard.png`, and this plan.
- **Dependencies on other chunks:** None.
- **Non-goals:** Curve interpolation/smoothing, data resampling, changing line
  widths/colors, antialiasing axes/text/bars, or filling missing intervals.
- **Commands to run:** sample renderer; focused renderer tests; full unittest
  discovery; compileall; `git diff --check`; PNG validation; local/public endpoint
  checks.
- **Expected checks:** diagonal edges contain intermediate grayscale coverage,
  stroke cores retain intended colors, missing-bucket gaps remain white, output
  geometry/encoding is unchanged, all tests pass, and live traces look smoother.
- **Blocker-report format:** `Chunk 1 blocked: <issue>; evidence: <output>;
  attempted: <approaches>; decision needed: <choice>.`

## Subagent Packets

This is one small renderer-only chunk requiring direct visual inspection; no
subagent packet is needed.

## Acceptance Criteria

- Today/Yesterday trace diagonals and endpoint markers have visibly smoother edges.
- Stroke centers retain black Today and light-gray Yesterday contrast.
- Missing intervals remain disconnected.
- Typography, axes, bars, data, layout, routes, and dimensions are unchanged.
- The final image remains a valid 1872x1404 4-bit grayscale PNG.
- The full test suite passes and a new public fingerprint is deployed.

## Test Plan

- Render a deterministic diagonal trace patch and assert it includes white,
  intended core color, and intermediate antialiasing levels.
- Assert the layer dimensions match its native target rectangle after resampling.
- Retain gap-segmentation tests and existing renderer pixel/geometry tests.
- Render and visually compare mock/live output at native resolution.
- Run focused/full tests, compileall, diff check, PNG inspection, service restart,
  and public Redirect/image checks.

## Linear Links

Not configured.

## Progress Log

- 2026-08-07: Plan created for targeted plot-trace antialiasing.
- 2026-08-07: Chunk 1 DONE. Added isolated 3x supersampling and Lanczos
  downsampling for both traces and the Today endpoint marker. All 14 focused
  renderer tests and the full 68-test suite pass with compileall, diff, and exact
  4-bit PNG checks.
- 2026-08-07: Final review found baseline endpoint markers were clipped by the
  original exact-size patch. Added five pixels of protected padding inside the
  six-pixel detached-spine gap and reverified full markers, crisp spines, and live
  output. No remaining correctness, regression, or unrelated-change findings.
  Published public fingerprint `14fe40ab63819b23`.
