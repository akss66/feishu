# Task 6 Report: B-type Daily Report Composition and Persistence

## Delivered

- Added an `Asia/Shanghai` report window covering the previous day at 09:00
  inclusive through the report day at 09:00 exclusive, converted to UTC for
  storage queries.
- Added B-type and empty-day health report composition with dynamic 0 or 1-15
  event selection, event-fingerprint deduplication, official-source preference,
  and risk/confidence/recency ordering.
- Added all-platform source coverage messaging that distinguishes enabled
  platforms with no verified update from platforms without an allowed, enabled
  source.
- Added profile-aware payloads. The current group profile is loaded for every
  preview and appears in the payload and title. Conservative reports replace
  model-authored actions with a fixed verification action; aggressive 60-74
  entries are labeled `早期信号·待核实` and expose only a fixed,
  reversible preparation action; the default profile preserves verified model
  actions.
- Added report repository queries restricted to current document versions,
  allowed sources, the exact fetched-at window, and confidence `>= 60`.
- Added unique group/day report persistence, immutable sent reports, idempotent
  preview transitions, and an atomic previewed-to-queued transition that creates
  one `daily:{group_id}:{report_date}` Outbox row.
- Added `DailyReportService` so preview and queue flows share the same window,
  coverage, profile, composition, and persistence behavior.

## TDD Evidence

The first correctly configured focused run failed during collection with:

```text
ModuleNotFoundError: No module named 'commerce_agent.intelligence.reports'
```

After the minimum implementation and review-driven regression fixes, the focused
report/repository suite passed:

```text
28 passed
```

Tests cover exact window boundaries, current-version/allowed-source/confidence
filters, 15-item capping without padding, official-source deduplication, ranking,
empty health reports, coverage counts, all three profile behaviors, group-profile
loading, report immutability, and preview/queue idempotency.

## Review

- Reviewed correctness, readability, architecture, security, and performance.
- Replaced per-analysis platform lookups with one batched source-platform query.
- Added review-driven RED→GREEN regressions for official-source priority across
  tied events and for queued/sent state-machine behavior. The follow-up review
  found no remaining critical or required issue.
- Added no dependency, network call, credential access, `.env` change, or
  sensitive logging.

## Verification

The worktree package is not installed in the active interpreter and that Python
distribution ignores `PYTHONPATH`, so pytest was invoked through `pytest.main()`
after prepending this worktree's `src` directory to `sys.path`.

```text
Focused pytest: 28 passed
Full pytest: 446 passed, 1 skipped
Ruff: All checks passed!
compileall: exit 0
git diff --check: exit 0 (Git emitted only line-ending conversion warnings)
```

The full test run emitted only pre-existing third-party `pkg_resources`
deprecation warnings from `lark_channel`.
