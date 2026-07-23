# Daily Report Test Isolation and Safe Resend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent test sends from consuming an official daily-report date, add audited test/correction deliveries, and safely resend the complete 2026-07-23 report.

**Architecture:** Separate report composition from official report persistence. Official, test, and correction deliveries retain the same trusted card payload but use closed idempotency namespaces; the CLI guards open report windows and exposes explicit confirmed test/resend commands. The scheduler treats an already-sent official report as an idempotent no-op while preserving all other failures.

**Tech Stack:** Python 3.11, asyncio, SQLAlchemy 2.x, SQLite, APScheduler 3.x, pytest/pytest-asyncio, Ruff, Feishu channel integration.

## Global Constraints

- Keep official idempotency keys exactly `daily:<group>:<date>`.
- Use `daily-test:<group>:<date>:<payload-digest>` and `daily-correction:<group>:<date>:<payload-digest>` only for their named variants.
- Do not delete or overwrite existing `sent` daily reports or outbox rows.
- Both new sending commands require `--confirm`.
- Do not print group IDs, payload content, credentials, or model output in CLI results or logs.
- Do not alter the 09:00 `Asia/Shanghai` schedule, risk profile, source configuration, or `.env`.
- Every production-code change follows a witnessed RED/GREEN test cycle and an atomic commit.

---

### Task 1: Reject Official Sends While the Report Window Is Open

**Files:**
- Modify: `src/commerce_agent/intelligence/reports.py`
- Modify: `src/commerce_agent/intelligence_cli.py`
- Test: `tests/unit/test_intelligence_reports.py`
- Test: `tests/unit/test_intelligence_cli.py`

**Interfaces:**
- Produces: `ReportWindowOpen(RuntimeError)`.
- Changes: `DailyReportService.queue_previewed(group_id: str, report_date: date) -> int` raises `ReportWindowOpen` when `clock() < report_window(...)[1]`.
- Produces CLI safe error: `report_window_open`.

- [ ] **Step 1: Write the failing service test**

```python
async def test_queue_previewed_rejects_a_report_whose_window_is_still_open() -> None:
    service, repository = _service(clock=lambda: datetime(2026, 7, 22, 5, tzinfo=UTC))

    with pytest.raises(ReportWindowOpen):
        await service.queue_previewed("chat-one", date(2026, 7, 23))

    assert repository.queued_report_ids == []
```

- [ ] **Step 2: Run the service test and verify RED**

Run: `python -m pytest tests/unit/test_intelligence_reports.py::test_queue_previewed_rejects_a_report_whose_window_is_still_open -q`

Expected: FAIL because `ReportWindowOpen` or the guard does not exist.

- [ ] **Step 3: Implement the minimal report-window guard**

```python
class ReportWindowOpen(RuntimeError):
    pass

async def queue_previewed(self, group_id: str, report_date: date) -> int:
    _, window_end = report_window(report_date, self._timezone)
    now = self._clock()
    if now < window_end:
        raise ReportWindowOpen("daily report window is still open")
    report_id = await self._repository.get_report_id(group_id, report_date)
    return await self._repository.queue_report(report_id, now=now)
```

- [ ] **Step 4: Add the failing CLI error-mapping test, then implement mapping**

```python
async def test_open_report_window_returns_a_safe_cli_error() -> None:
    app = FakeCliApplication(failure=ReportWindowOpen("private detail"))
    code, output = await invoke(
        ["report", "send", "--date", "2026-07-23", "--confirm"], app
    )
    assert code == 3
    assert output == "error=report_window_open\n"
```

Run before implementation: `python -m pytest tests/unit/test_intelligence_cli.py::test_open_report_window_returns_a_safe_cli_error -q`

Expected: FAIL with `runtime_error`; add `ReportWindowOpen` to `controlled_cli_error` and rerun both targeted tests.

- [ ] **Step 5: Verify Task 1 and commit**

Run: `python -m pytest tests/unit/test_intelligence_reports.py tests/unit/test_intelligence_cli.py -q`

Run: `python -m ruff check src/commerce_agent/intelligence/reports.py src/commerce_agent/intelligence_cli.py tests/unit/test_intelligence_reports.py tests/unit/test_intelligence_cli.py`

Commit:

```powershell
git add src/commerce_agent/intelligence/reports.py src/commerce_agent/intelligence_cli.py tests/unit/test_intelligence_reports.py tests/unit/test_intelligence_cli.py
git commit -m "fix: protect open daily report windows"
```

---

### Task 2: Queue Audited Test and Correction Variants

**Files:**
- Modify: `src/commerce_agent/intelligence/repository.py`
- Modify: `src/commerce_agent/intelligence/reports.py`
- Test: `tests/integration/test_intelligence_repository.py`
- Test: `tests/unit/test_intelligence_reports.py`

**Interfaces:**
- Produces: `SqlAlchemyIntelligenceRepository.queue_report_variant(group_id: str, draft: DailyReportDraft, *, variant: Literal["test", "correction"], now: datetime) -> int`.
- Produces: `DailyReportService.build(group_id: str, report_date: date) -> DailyReportDraft`.
- Produces: `DailyReportService.generate_variant_and_queue(group_id: str, report_date: date, *, variant: Literal["test", "correction"]) -> int`.

- [ ] **Step 1: Write failing repository integration tests**

```python
async def test_report_variant_uses_separate_idempotency_and_does_not_create_daily_report(
    database: Database,
) -> None:
    repository = SqlAlchemyIntelligenceRepository(database.session)
    draft = DailyReportComposer().compose(report_date=date(2026, 7, 23), analyses=())

    first = await repository.queue_report_variant(
        "chat-one", draft, variant="correction", now=NOW
    )
    second = await repository.queue_report_variant(
        "chat-one", draft, variant="correction", now=NOW
    )

    assert first == second
    # Assert one DAILY_REPORT outbox row with a daily-correction prefix.
    # Assert no DailyReport row exists for chat-one/2026-07-23.
```

Also assert `variant="unknown"` raises `ValueError` before opening a transaction.

- [ ] **Step 2: Run repository tests and verify RED**

Run: `python -m pytest tests/integration/test_intelligence_repository.py -k report_variant -q`

Expected: FAIL because `queue_report_variant` does not exist.

- [ ] **Step 3: Implement the minimal repository method**

```python
async def queue_report_variant(self, group_id, draft, *, variant, now):
    if variant not in {"test", "correction"}:
        raise ValueError("unsupported report delivery variant")
    rendered = json.dumps(draft.payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]
    key = f"daily-{variant}:{group_id}:{draft.report_date.isoformat()}:{digest}"
    # Insert one DAILY_REPORT outbox row with ON CONFLICT DO NOTHING and return its id.
```

Copy the trusted payload before prefixing its title with `测试 · ` or `补发 · `; do not mutate `draft.payload`.

- [ ] **Step 4: Add service build/variant tests and implement composition separation**

```python
async def test_variant_build_does_not_save_an_official_report() -> None:
    service, repository = _service(clock=lambda: NOW)
    outbox_id = await service.generate_variant_and_queue(
        "chat-one", date(2026, 7, 23), variant="test"
    )
    assert outbox_id == repository.variant_outbox_id
    assert repository.saved_reports == []
```

Extract the existing query/compose portion of `preview` into `build`; keep `preview` responsible for `save_report` and `mark_report_previewed` only.

- [ ] **Step 5: Verify Task 2 and commit**

Run: `python -m pytest tests/unit/test_intelligence_reports.py tests/integration/test_intelligence_repository.py -q`

Run: `python -m ruff check src/commerce_agent/intelligence/reports.py src/commerce_agent/intelligence/repository.py tests/unit/test_intelligence_reports.py tests/integration/test_intelligence_repository.py`

Commit:

```powershell
git add src/commerce_agent/intelligence/reports.py src/commerce_agent/intelligence/repository.py tests/unit/test_intelligence_reports.py tests/integration/test_intelligence_repository.py
git commit -m "feat: isolate report delivery variants"
```

---

### Task 3: Expose Confirmed Test-Send and Resend Commands

**Files:**
- Modify: `src/commerce_agent/intelligence_cli.py`
- Test: `tests/unit/test_intelligence_cli.py`
- Modify: `docs/operations/intelligence-delivery-runbook.md`

**Interfaces:**
- Adds commands: `report test-send --date YYYY-MM-DD --confirm` and `report resend --date YYYY-MM-DD --confirm`.
- Adds application methods: `test_send_report(report_date: date)` and `resend_report(report_date: date)`.

- [ ] **Step 1: Write failing CLI parser and confirmation tests**

```python
@pytest.mark.parametrize("command", ["test-send", "resend"])
async def test_report_variant_send_requires_confirmation(command: str) -> None:
    app = FakeCliApplication()
    code, output = await invoke(["report", command, "--date", "2026-07-23"], app)
    assert code == 2
    assert output == "error=confirm_required\n"
    assert app.calls == []
```

Add confirmed dispatch tests asserting `test_send_report` and `resend_report` are called exactly once.

- [ ] **Step 2: Run CLI tests and verify RED**

Run: `python -m pytest tests/unit/test_intelligence_cli.py -k "variant or test_send or resend" -q`

Expected: FAIL because the subcommands and application methods do not exist.

- [ ] **Step 3: Implement parser dispatch and production methods**

Both methods call `generate_variant_and_queue`, then the existing `delivery.send_id`, and return only safe aggregate fields:

```python
return {
    "status": "sent" if summary.sent else "partial",
    "sent": summary.sent,
    "failed": summary.failed,
    "skipped": summary.skipped,
}
```

- [ ] **Step 4: Document operational commands**

Add exact commands, confirmation requirements, isolation guarantees, and the rule that `report send` rejects an open report window to `docs/operations/intelligence-delivery-runbook.md`.

- [ ] **Step 5: Verify Task 3 and commit**

Run: `python -m pytest tests/unit/test_intelligence_cli.py -q`

Run: `python -m ruff check src/commerce_agent/intelligence_cli.py tests/unit/test_intelligence_cli.py`

Run: `git diff --check`

Commit:

```powershell
git add src/commerce_agent/intelligence_cli.py tests/unit/test_intelligence_cli.py docs/operations/intelligence-delivery-runbook.md
git commit -m "feat: add safe report test and resend commands"
```

---

### Task 4: Treat Already-Sent Scheduled Reports as Idempotent

**Files:**
- Modify: `src/commerce_agent/intelligence/scheduler.py`
- Test: `tests/unit/test_intelligence_scheduler.py`

**Interfaces:**
- Changes `_run_daily` only: catches `ReportAlreadySent` separately, logs a content-free informational event, and retains existing error containment for all other exceptions.

- [ ] **Step 1: Write the failing scheduler test**

```python
async def test_already_sent_daily_report_is_an_idempotent_scheduler_noop(caplog) -> None:
    scheduler, backend, *_ = build_scheduler()
    scheduler._reports = AlreadySentReports()
    scheduler.start()
    with caplog.at_level(logging.INFO):
        await backend.jobs[DAILY_JOB_ID][0]()
    assert "daily report already sent; skipping" in caplog.text
    assert "intelligence daily job failed" not in caplog.text
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/unit/test_intelligence_scheduler.py::test_already_sent_daily_report_is_an_idempotent_scheduler_noop -q`

Expected: FAIL because the existing generic exception branch logs an error.

- [ ] **Step 3: Implement the narrow exception branch**

Import `ReportAlreadySent`, catch it immediately before `except Exception`, and log only a stable event name without the group ID or exception message.

- [ ] **Step 4: Verify Task 4 and commit**

Run: `python -m pytest tests/unit/test_intelligence_scheduler.py -q`

Run: `python -m ruff check src/commerce_agent/intelligence/scheduler.py tests/unit/test_intelligence_scheduler.py`

Commit:

```powershell
git add src/commerce_agent/intelligence/scheduler.py tests/unit/test_intelligence_scheduler.py
git commit -m "fix: make daily scheduler idempotent"
```

---

### Task 5: Full Verification, Runtime Restart, and Audited Resend

**Files:**
- Verify: `docs/operations/intelligence-delivery-runbook.md`
- Runtime artifact: existing SQLite outbox and Feishu message audit rows; do not commit runtime data.

**Interfaces:**
- Uses: `python -m commerce_agent.intelligence_cli report resend --date 2026-07-23 --confirm`.

- [ ] **Step 1: Run complete verification**

Run: `python -m pytest`

Expected: all tests pass.

Run: `python -m ruff check .`

Expected: `All checks passed!`

Run: `git diff --check`

Expected: no output and exit 0.

- [ ] **Step 2: Restart only the managed task and verify one process**

Stop and start `CrossBorderCommerceAgent` through Task Scheduler. Confirm task state `Running`, exactly one process whose command line is `python.exe -m commerce_agent`, and scheduler startup entries in the runtime log.

- [ ] **Step 3: Build a non-sending preview for 2026-07-23**

Run: `python -m commerce_agent.intelligence_cli report preview --date 2026-07-23`

Expected: this may return `report_already_sent` because the official record is immutable. If so, use the new `resend` path's build-only unit/integration evidence and inspect aggregate eligible-analysis counts read-only; do not reset the sent row.

- [ ] **Step 4: Execute exactly one confirmed correction send**

Run: `python -m commerce_agent.intelligence_cli report resend --date 2026-07-23 --confirm`

Expected: `failed=0 sent=1 status=sent`.

- [ ] **Step 5: Verify delivery audit and idempotency read-only**

Query only aggregate/safe fields for the newest `daily-correction:` outbox row. Confirm `status=sent`, `attempt_count>=1`, `safe_error_code IS NULL`, and `feishu_message_id IS NOT NULL`. Re-running the command is not part of acceptance because it would intentionally exercise external delivery; automated integration tests prove duplicate idempotency.

- [ ] **Step 6: Commit any final documentation-only evidence and report handoff**

Do not commit `commerce_agent.db`, `.env`, or runtime logs. Report the sent status, selected count, correction audit ID, task state, and remaining historical failure count without exposing group IDs or message IDs.
