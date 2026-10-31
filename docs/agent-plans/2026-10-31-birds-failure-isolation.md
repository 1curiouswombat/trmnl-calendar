# Bird plugin failure isolation

## Goal
Keep calendar endpoints available when the optional bird snapshot cannot render, and return 404 for disabled bird routes.

## Constraints
Preserve existing plugin behavior and cached bird images. Do not require bird configuration for other plugins.

## Discovered Context
Bird registration checks only path existence. Initial cache rendering happens before the HTTP server starts; a bird render failure currently aborts startup. Routes resolve independently of enabled plugins.

## Alternatives Considered
- Validate snapshots at registration: misses failures during rendering and later file changes.
- Isolate optional initial render failures and let requests retry: covers all render failures while preserving cached results and existing error handling.

## Final Design
Catch and log initial bird render failures without aborting startup; keep its cache registered so later requests and prewarming retry. Return 404 when a resolved route has no registered cache.

## Chunk List
One chunk: server startup and request handling, with focused tests. No dependencies; no changes to data selection or other plugins. Run `PYTHONPATH=src python3 -m pytest -q`. Expected: all tests pass, including failures and recovery. Report blockers with command and observed error.

## Subagent Packets
None; this is a small cohesive change.

## Acceptance Criteria
Disabled bird routes return 404. Invalid bird snapshot does not stop server startup or other endpoints, and corrected data can subsequently render. Failures for required plugins retain existing behavior.

## Test Plan
Exercise actual HTTP responses and initial cache setup with an invalid then corrected bird snapshot; run full test suite.

## Linear Links
Not configured for this task.

## Progress Log
Plan created before implementation.
