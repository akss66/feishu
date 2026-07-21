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

## Reviewer fixes

The review findings were verified against the committed implementation. The atomic
selector did not inspect current source compliance, and an expired running job at
`attempt_count == 2` could be reclaimed and incremented to a third attempt. The stale
failure guard and completion transaction rollback were already present, so those two
findings required real-database regression coverage rather than production changes.

### RED evidence

Four integration tests were added before changing production code: denied-source
filtering, terminalization of an expired second lease, stale old-token failure, and
rollback when the unique `DocumentAnalysis` insert fails.

```powershell
python -c "import sys,pytest; sys.path.insert(0, r'C:\Users\AKSSINA\Desktop\feishu\.worktrees\ai-intelligence-delivery\src'); raise SystemExit(pytest.main(['tests/integration/test_intelligence_repository.py','-v']))"
```

```text
collected 10 items
tests\integration\test_intelligence_repository.py .F.F......             [100%]
FAILED tests/integration/test_intelligence_repository.py::test_claim_skips_jobs_from_denied_sources
E AssertionError: assert AnalysisCandidate(...) is None
FAILED tests/integration/test_intelligence_repository.py::test_expired_second_lease_is_failed_without_a_third_claim
E AssertionError: assert AnalysisCandidate(...) is None
========================= 2 failed, 8 passed in 1.24s =========================
```

The two passing regression tests in this RED run established that stale
`fail_analysis` already raises without mutating the fresh lease, and a forced analysis
insert integrity error already rolls back the guarded completed/token transition.

### GREEN evidence

The atomic scalar subquery now joins through `DocumentVersion` and `Document` to
`Source` and filters `Source.compliance == 'allowed'`. In the same SQLite write
transaction, exhausted expired running jobs are atomically changed to `failed` with
`error_code='lease_expired'` before the bounded `UPDATE ... RETURNING` claim runs;
expired running jobs are reclaimable only while `attempt_count < 2`.

Focused command (same as RED):

```text
collected 10 items
tests\integration\test_intelligence_repository.py ..........             [100%]
============================= 10 passed in 1.05s ==============================
```

Covering integration command:

```powershell
python -c "import sys,pytest; sys.path.insert(0, r'C:\Users\AKSSINA\Desktop\feishu\.worktrees\ai-intelligence-delivery\src'); raise SystemExit(pytest.main(['tests/integration/test_intelligence_repository.py','tests/integration/test_ingestion_repository.py','tests/integration/test_ingestion_pipeline.py','-v']))"
```

```text
collected 24 items
tests\integration\test_intelligence_repository.py ..........             [ 41%]
tests\integration\test_ingestion_repository.py .........                 [ 79%]
tests\integration\test_ingestion_pipeline.py .....                       [100%]
============================= 24 passed in 2.74s ==============================
```

Static verification:

```powershell
python -m ruff check src/commerce_agent/intelligence/repository.py tests/integration/test_intelligence_repository.py
git diff --check
```

```text
All checks passed!
```

Final full-suite command:

```powershell
python -c "import sys,pytest; sys.path.insert(0, r'C:\Users\AKSSINA\Desktop\feishu\.worktrees\ai-intelligence-delivery\src'); raise SystemExit(pytest.main([]))"
```

```text
343 passed, 1 skipped, 5 warnings in 7.76s
```

The five warnings remain the same out-of-scope third-party `lark_channel` /
`pkg_resources` deprecations documented above.
