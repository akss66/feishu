# Task 2 Report: Recoverable three-failure source circuit breaker

## Status

Complete. A source is suspended after three consecutive `partial` or `failed`
runs. Scheduled execution short-circuits suspended sources without acquiring a
lease or storing a fetch run. Manual execution remains available and a successful
manual run restores the existing healthy, zero-failure state.

## TDD evidence

### Repository RED

Before changing production code, the integration test was added to cover two
consecutive unsuccessful results, a third `partial` or `failed` result, and a
successful manual recovery. The first collection run exposed a missing `pytest`
import in the new parameterized test; that test-only setup error was fixed before
any production edit. The intended RED command then failed exactly because the new
repository contract did not exist:

```powershell
python -m pytest tests/integration/test_ingestion_repository.py -q
```

```text
.....FF......
AttributeError: 'SqlAlchemyIngestionRepository' object has no attribute
'is_source_suspended'
2 failed, 11 passed
```

### Repository GREEN

The minimal production change added `SOURCE_FAILURE_THRESHOLD: Final[int] = 3`,
the protocol and SQLAlchemy `is_source_suspended` query, and status selection after
incrementing `consecutive_failures`. At or above the threshold it stores
`suspended`; below the threshold it retains `degraded` for partial and `error` for
failed runs. Existing successful-run behavior resets the count and health.

```powershell
python -m pytest tests/integration/test_ingestion_repository.py -q
```

```text
.............                                                            [100%]
13 passed
```

### Service RED

Before changing service code, `FakeRepository` gained a suspended-source set and
query method. New tests require a scheduled suspended source to avoid collector,
lease, and fetch-run creation, while a manual run proceeds normally.

```powershell
python -m pytest tests/unit/test_ingestion_service.py -q
```

```text
.....F......................
AssertionError: RunStatus.SUCCESS is not RunStatus.SKIPPED
1 failed, 27 passed
```

### Service GREEN

`source_circuit_open` was added to the controlled known error codes. Following
source synchronization and before lease acquisition, scheduled runs query the
repository and return `SKIPPED` when suspended. Manual triggers intentionally bypass
the branch.

```powershell
python -m pytest tests/unit/test_ingestion_service.py -q
```

```text
............................                                             [100%]
28 passed
```

## Final verification

The task-required related test set was run once after implementation, followed by
the requested static checks:

```powershell
python -m pytest tests/integration/test_ingestion_repository.py tests/unit/test_ingestion_service.py tests/unit/test_ingestion_cli.py -q
python -m ruff check src/commerce_agent/persistence/ingestion.py src/commerce_agent/ingestion/service.py tests/integration/test_ingestion_repository.py tests/unit/test_ingestion_service.py
git diff --check
```

```text
61 passed
All checks passed!
git diff --check exited 0
```

Git reported only existing CRLF conversion warnings while reading the worktree;
they do not indicate whitespace errors.

## Self-review

- Correctness: parameterized repository coverage proves both `partial` and
  `failed` can be the threshold-closing result; the recovery assertion verifies the
  failure count, health status, and circuit query all return to normal.
- Architecture: suspension state remains owned by persistence, while the service
  owns the scheduled-only orchestration decision. The public repository protocol
  makes the dependency explicit for production and fakes.
- Security: the added database lookup is a SQLAlchemy expression with a bound source
  identifier; no sensitive values, logging changes, or external inputs were added.
- Performance: the circuit check is a single scalar indexed primary-key lookup and
  avoids lease/run creation and collector work for open circuits.
- Scope: only the four brief-owned source/test files changed for implementation;
  this report is the requested implementation record.

## Concerns

None. No new dependencies, migrations, or out-of-scope source-registry changes were
required.

## Reviewer fix: skipped manual runs retain the open circuit

### RED evidence

The reviewer found that `finish_run` assigned `healthy` for every `SKIPPED`
summary. A manual bypass that was skipped by an existing policy (for example,
`source_disabled`) could therefore clear a suspended circuit despite not succeeding.
Before changing production code, an integration regression test was added for three
failed scheduled runs followed by a skipped manual run. It failed for the reported
state transition:

```powershell
python -m pytest tests/integration/test_ingestion_repository.py -q
```

```text
.......F......
AssertionError: assert 'healthy' == 'suspended'
1 failed, 13 passed
```

### GREEN evidence

The `SKIPPED` branch now preserves an existing `suspended` health status; it retains
the prior `healthy` assignment for all other health states. The existing `SUCCESS`
branch remains the only manual-bypass outcome that resets failures and restores
health. The threshold behavior is unchanged and remains covered by the prior
parameterized test.

```powershell
python -m pytest tests/integration/test_ingestion_repository.py -q
```

```text
..............                                                           [100%]
14 passed
```

### Reviewer-fix final verification

```powershell
python -m pytest tests/integration/test_ingestion_repository.py tests/unit/test_ingestion_service.py tests/unit/test_ingestion_cli.py -q
python -m ruff check src/commerce_agent/persistence/ingestion.py src/commerce_agent/ingestion/service.py tests/integration/test_ingestion_repository.py tests/unit/test_ingestion_service.py
git diff --check
```

```text
62 passed
All checks passed!
git diff --check exited 0
```
