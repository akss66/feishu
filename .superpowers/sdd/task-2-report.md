# Task 2 Report: Persistent analysis jobs and transactional enqueue

## Status

DONE_WITH_CONCERNS. The requested persistence tables, transactional enqueue, atomic
SQLite claim, stale-token protection, controlled retry state, completion persistence,
and job backfill are implemented. All focused and full-suite tests pass. The only
concern is five pre-existing third-party deprecation warnings from `lark_channel` /
`pkg_resources` in the full suite.

## TDD RED evidence

Before any production code was changed, the enqueue and intelligence repository tests
were added and run with the worktree `src` directory inserted at the front of
`sys.path`:

```powershell
python -c "import sys,pytest; sys.path.insert(0, r'C:\Users\AKSSINA\Desktop\feishu\.worktrees\ai-intelligence-delivery\src'); raise SystemExit(pytest.main(['tests/integration/test_intelligence_repository.py','tests/integration/test_ingestion_repository.py','-v']))"
```

Result: exit 1 during collection, for the expected missing feature:

```text
collected 0 items / 2 errors
E   ModuleNotFoundError: No module named 'commerce_agent.intelligence.repository'
E   ImportError: cannot import name 'AnalysisJob' from
    'commerce_agent.persistence.models'
=========================== short test summary info ===========================
ERROR tests/integration/test_intelligence_repository.py
ERROR tests/integration/test_ingestion_repository.py
!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!
============================== 2 errors in 0.37s ==============================
```

This was the intended RED: neither the repository nor the analysis job table existed.

## GREEN evidence

After the minimal schema, enqueue, and repository implementation, the same focused
command was rerun. One intermediate collection error showed that `tests` is not an
importable package; the new test file was made self-contained without changing
production behavior. The focused command then passed:

```text
collected 15 items
tests\integration\test_intelligence_repository.py ......                 [ 40%]
tests\integration\test_ingestion_repository.py .........                 [100%]
============================= 15 passed in 1.44s ==============================
```

The task-specified integration scope was then run with the same explicit worktree
import strategy:

```powershell
python -c "import sys,pytest; sys.path.insert(0, r'C:\Users\AKSSINA\Desktop\feishu\.worktrees\ai-intelligence-delivery\src'); raise SystemExit(pytest.main(['tests/integration/test_intelligence_repository.py','tests/integration/test_ingestion_repository.py','tests/integration/test_ingestion_pipeline.py','-v']))"
```

```text
collected 20 items
tests\integration\test_intelligence_repository.py ......                 [ 30%]
tests\integration\test_ingestion_repository.py .........                 [ 75%]
tests\integration\test_ingestion_pipeline.py .....                       [100%]
============================= 20 passed in 2.46s ==============================
```

## Full-suite verification

Command:

```powershell
python -c "import sys,pytest; sys.path.insert(0, r'C:\Users\AKSSINA\Desktop\feishu\.worktrees\ai-intelligence-delivery\src'); raise SystemExit(pytest.main([]))"
```

Result:

```text
339 passed, 1 skipped, 5 warnings in 6.21s
```

The five warnings are existing `pkg_resources` deprecation warnings originating from
the installed `lark_channel` package. Static verification also passed:

```powershell
python -m ruff check src/commerce_agent/persistence/models.py src/commerce_agent/persistence/ingestion.py src/commerce_agent/intelligence/repository.py tests/integration/test_intelligence_repository.py tests/integration/test_ingestion_repository.py
git diff --check
```

```text
All checks passed!
```

## Files changed

- `src/commerce_agent/persistence/models.py`: added `AnalysisJob`,
  `DocumentAnalysis`, `DailyReport`, and `DeliveryOutbox` with the required uniqueness,
  foreign-key, and due-query indexes.
- `src/commerce_agent/persistence/ingestion.py`: inserts exactly one pending analysis
  job in the same transaction as each newly created immutable document version.
- `src/commerce_agent/intelligence/repository.py`: implements single-statement
  `UPDATE ... RETURNING` lease claims, guarded completion/failure transitions, and
  bounded idempotent backfill.
- `tests/integration/test_intelligence_repository.py`: covers atomic competing claims,
  lease reclamation and stale completion, persisted analysis payloads, tokenless claim
  rejection, retry/failure state transitions, and bounded backfill.
- `tests/integration/test_ingestion_repository.py`: covers exactly-once enqueue under
  duplicate version persistence.

## Self-review

- Correctness: claim eligibility is exactly pending, due retry, or expired running;
  every claim receives a new 32-character token and increments attempts. Completion
  and failure guard on job id, token, and running status. Completion writes analysis
  only after the guarded transition and shares its transaction, so insertion failure
  rolls the job transition back. First failure schedules five minutes; second fails
  terminally. Backfill is bounded and conflict-safe.
- Readability/architecture: the persistence repository consumes Task 1 immutable
  contracts and follows the existing async SQLAlchemy session-factory pattern. No new
  dependency or unrelated refactor was introduced.
- Security: all SQL is SQLAlchemy expression-based and parameterized; lease tokens are
  random UUID4 hex values; stale and null tokens cannot mutate jobs.
- Performance: claim and backfill are bounded; required due and lookup indexes exist;
  candidate hydration uses one joined row query plus one bounded platform query.
- Scope: only the brief-owned source/test files plus this required report changed.

## Concerns

- The full suite is green but not warning-free because the existing Feishu SDK emits
  five `pkg_resources` deprecation warnings. No task-owned code can remove those
  warnings without an out-of-scope dependency change.
