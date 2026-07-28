# Final Critical Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two residual Critical findings: unsafe catch-all article attribution and non-global/non-exact media snapshot retention.

**Architecture:** Article platform attribution fails closed at the document-version boundary. Recoverable legacy versions are deterministically backfilled from their persisted extracted body; unrecoverable versions remain unknown and are excluded from platform-specific claims. Retention runs independently of collection scheduling and prunes timestamped snapshots by exact UTC cutoff, including orphan snapshots, while isolating filesystem and database failures.

**Tech Stack:** Python 3, SQLAlchemy, SQLite/PostgreSQL-compatible ORM, APScheduler, pytest.

## Global Constraints

- No network requests or live smoke runs during implementation or verification.
- Preserve SSRF, exact-host/path, IDNA, DNS-pinning, article-rights/access-wall, request-budget, and log-redaction controls.
- Strict audited coverage remains `3/10、3/20`.
- Temporary media full-text bodies and raw snapshots must not exceed exactly seven days.
- Use TDD: every behavior-changing production edit must be preceded by a test that fails for the expected reason.
- Do not introduce secrets, browser state, proxies, login, CAPTCHA handling, or new third-party dependencies.

---

### Task 1: Fail-closed legacy article platform attribution

**Files:**
- Modify: `src/commerce_agent/persistence/ingestion.py`
- Modify: `src/commerce_agent/intelligence/repository.py`
- Modify as needed: persistence models/migration helpers used by document-version platforms
- Test: `tests/integration/test_ingestion_repository.py`
- Test: `tests/integration/test_intelligence_repository.py`

**Interfaces:**
- Consumes: `DocumentVersionPlatform`, deterministic platform matcher, persisted extracted body.
- Produces: exact version-platform rows or an explicit unknown/empty mapping; never static catch-all fallback for article-varying sources.

- [ ] **Step 1: Write failing pre-upgrade regressions**

Create legacy direct-media versions with no `DocumentVersionPlatform` rows and prove:

```python
# Recoverable Amazon-only body
assert claimed.platforms == ("amazon",)
assert report_platforms == {"amazon"}
assert corpus_query("temu") == []

# Unrecoverable/empty body
assert claimed.platforms == ()
assert corpus_query("amazon") == []
```

Also assert a newly collected catch-all direct-media item with an empty exact platform set is rejected/fails closed instead of copying all ten `SourcePlatform` rows.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/integration/test_ingestion_repository.py tests/integration/test_intelligence_repository.py -q
```

Expected: failures demonstrate the current static `SourcePlatform` fallback.

- [ ] **Step 3: Implement deterministic backfill and fail-closed reads/writes**

- For article-varying direct-media sources, never populate version platforms from static source platforms.
- Backfill a legacy version once from its persisted extracted body using the deterministic matcher.
- Persist recovered exact rows transactionally.
- Leave unrecoverable versions explicitly unmapped and exclude them from platform-specific analysis, alert, report, reference, and corpus results.
- Preserve static fallback only for sources whose platform relation is intrinsically fixed and cannot vary per article.

- [ ] **Step 4: Verify GREEN and strict baseline**

Run the focused tests plus production-registry coverage integration. Expected: Amazon-only stays Amazon-only, unknown stays unknown, and strict coverage is `3/10、3/20`.

- [ ] **Step 5: Commit**

```powershell
git add -- src tests
git commit -m "fix: fail closed on legacy article platforms"
```

---

### Task 2: Always-on exact seven-day retention

**Files:**
- Modify: `src/commerce_agent/runtime.py`
- Modify: `src/commerce_agent/ingestion/scheduler.py`
- Modify: `src/commerce_agent/ingestion/service.py`
- Modify: `src/commerce_agent/ingestion/snapshots.py`
- Modify as needed: retention repository interfaces
- Test: `tests/unit/test_runtime.py`
- Test: `tests/unit/test_ingestion_scheduler.py`
- Test: `tests/unit/test_snapshot_store.py`
- Test: `tests/unit/test_ingestion_service.py`
- Test: `tests/integration/test_ingestion_repository.py`

**Interfaces:**
- Consumes: exact UTC cutoff, snapshot timestamp metadata/path, global retention repository operation.
- Produces: retention startup + dedicated schedule independent of `INGESTION_SCHEDULER_ENABLED`.

- [ ] **Step 1: Write failing retention regressions**

Prove all of these fail on current code:

```python
assert retention_started_when_collection_scheduler_disabled
assert snapshot_created_7_days_and_1_second_ago_is_deleted
assert snapshot_created_6_days_23_hours_59_minutes_ago_is_kept
assert orphan_snapshot_after_extraction_failure_is_deleted
assert orphan_snapshot_after_persistence_failure_is_deleted
assert database_redaction_runs_even_when_snapshot_pruning_raises
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_runtime.py tests/unit/test_ingestion_scheduler.py tests/unit/test_snapshot_store.py tests/unit/test_ingestion_service.py tests/integration/test_ingestion_repository.py -q
```

- [ ] **Step 3: Implement independent retention lifecycle**

- Construct/start retention whenever retained ingestion data is accessible, regardless of collection scheduler enablement.
- Keep collection jobs disabled when configured; run only startup retention and the dedicated retention interval.
- Store/index an exact UTC timestamp for every snapshot and prune with `created_at < cutoff`.
- Enumerate snapshots from the store itself so unlinked/orphan files are covered.
- Isolate snapshot and database cleanup: attempt both and return/record stable partial-failure diagnostics.
- Preserve analysis/short evidence/hash/attribution/original URL while redacting expired bodies and safely terminating pending analysis.

- [ ] **Step 4: Verify GREEN**

Run focused tests. Expected: all cases pass without network.

- [ ] **Step 5: Commit**

```powershell
git add -- src tests
git commit -m "fix: enforce global exact media retention"
```

---

### Task 3: Whole-branch verification and operator evidence

**Files:**
- Modify if behavior changed: `docs/operations/source-ingestion-runbook.md`
- Modify: `docs/operations/ten-platform-source-acceptance.md`

- [ ] **Step 1: Update operator documentation**

Document fail-closed legacy attribution, exact seven-day UTC behavior, orphan snapshot cleanup, and independent retention lifecycle. Do not claim new live evidence.

- [ ] **Step 2: Run complete offline verification**

```powershell
..\..\.venv\Scripts\python.exe -m pytest -q
..\..\.venv\Scripts\python.exe -m ruff format --check src tests
..\..\.venv\Scripts\python.exe -m ruff check src tests
git diff --check
```

Expected: all offline tests pass; only the three explicit live-network tests skip.

- [ ] **Step 3: Scan for secrets and commit**

Scan the complete remediation diff for credential-like values, verify `git status --short`, and commit only intended documentation changes.

- [ ] **Step 4: Independent review**

Review both residual Critical findings against the pre-upgrade, empty-platform, scheduler-disabled, timestamp-boundary, and orphan-snapshot tests. Ready is `Yes` only if both are `ADDRESSED` and there is no new Critical/Important issue.
