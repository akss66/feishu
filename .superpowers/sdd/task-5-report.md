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

## Important review correction: corroborating source counting

The review found that source corroboration was counted before the current analysis
was inserted. A second source therefore observed only one persisted source and lost
the 10-point multi-source component. Historical analyses for superseded document
versions were also not excluded.

The correction changes drain to two deterministic phases:

1. Analyze each bounded claim and compute its event fingerprint.
2. Group successful batch claims by fingerprint, then score and persist them.

For each score, the repository unions distinct sources from the successful persisted
corpus with distinct current batch sources. Persisted rows count only when their job
is completed, their source remains `allowed`, and their version is the document's
current version. Batch claims are checked against the same current-version and
compliance rules, and repeated documents from one source still count once.

Review-fix TDD evidence:

- RED: 7 expected failures, including the two-source service path scoring 90 instead
  of 100 and the old repository signature rejecting fingerprint/claim context.
- Focused GREEN: 31 passed.
- Real SQLite coverage proves two current sources receive the +10 component, two
  same-source rows do not, and a superseded matching analysis is excluded.

Review-fix final verification:

- Full pytest: `432 passed, 1 skipped` with the same 5 existing third-party
  `pkg_resources` warnings.
- Ruff: `All checks passed!`
- Compileall: exit code 0.
- `git diff --check`: exit code 0 with only Windows LF-to-CRLF warnings.
