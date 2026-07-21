# Task 5 Report: Analysis drain service

## Outcome

- Added `AnalysisService.drain(limit)` and immutable `AnalysisBatch` results.
- Claims at most `limit` jobs and bounds concurrent analysis with a semaphore.
- Preserves cancellation, maps failures to the five allowed safe codes, and avoids
  mutating a job after a stale lease is detected.
- Logs only the exception class, internal job id, and elapsed milliseconds.
- Scores evidence, resolves risk without a group profile, and returns the
  profile-independent `RiskResolution` in each `ScoredAnalysis`.
- Persists the resolved risk floor in `DocumentAnalysis.risk_level` while retaining
  the validated model risk unchanged in `structured_payload`.
- Added deterministic analysis listing and distinct-source corroboration queries.

## TDD evidence

1. Initial focused run failed during collection because
   `commerce_agent.intelligence.service` did not exist.
2. The minimal service and repository slice made the focused suite pass.
3. Provider exception tests were added next and failed for OpenAI timeout,
   connection, and 5xx exceptions; explicit type mappings then made them pass.

Focused result: `27 passed`.

## Final verification

- Full pytest: `428 passed, 1 skipped` (5 existing third-party `pkg_resources`
  deprecation warnings from `lark_channel`).
- Ruff: `All checks passed!`
- Compileall: exit code 0.
- `git diff --check`: exit code 0; Git emitted only the repository's Windows
  LF-to-CRLF checkout warning.

The local Python installation has a fixed path entry for the parent checkout, so
pytest was launched with the task worktree's `src` inserted first in `sys.path`.
No network calls or environment-file reads were used.
