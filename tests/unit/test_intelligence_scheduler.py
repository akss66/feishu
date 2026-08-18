from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from commerce_agent.intelligence.reports import ReportAlreadySent
from commerce_agent.intelligence.scheduler import (
    ANALYSIS_JOB_ID,
    DAILY_CATCHUP_JOB_ID,
    DAILY_JOB_ID,
    DAILY_PREPARE_JOB_ID,
    DELIVERY_JOB_ID,
    IntelligenceScheduler,
)


class FakeScheduler:
    def __init__(self, *, timezone: str) -> None:
        self.timezone = timezone
        self.jobs: dict[str, tuple[Callable[..., Any], dict[str, Any]]] = {}
        self.starts = 0
        self.shutdowns: list[bool] = []

    def add_job(self, callback: Callable[..., Any], **options: Any) -> None:
        self.jobs[options["id"]] = (callback, options)

    def start(self) -> None:
        self.starts += 1

    def shutdown(self, *, wait: bool) -> None:
        self.shutdowns.append(wait)


class Analysis:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def drain(self, *, limit: int) -> None:
        self.calls.append(limit)


class Reports:
    def __init__(self) -> None:
        self.preview_calls: list[tuple[str, object]] = []
        self.queue_calls: list[tuple[str, object]] = []

    async def preview(self, group_id: str, report_date: object) -> object:
        self.preview_calls.append((group_id, report_date))
        return object()

    async def queue_previewed(self, group_id: str, report_date: object) -> int:
        self.queue_calls.append((group_id, report_date))
        return 1


class Alerts:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime]] = []

    async def queue_due(self, group_id: str, *, now: datetime) -> tuple[int, ...]:
        self.calls.append((group_id, now))
        return ()


class Delivery:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def drain(self, *, limit: int) -> None:
        self.calls.append(limit)


class Bindings:
    def __init__(self, active_chat_id: str | None = "chat-one") -> None:
        self.active_chat_id = active_chat_id

    async def get_active_chat_id(self) -> str | None:
        return self.active_chat_id


def build_scheduler(
    *,
    analysis_enabled: bool = True,
    alerts_enabled: bool = True,
    daily_enabled: bool = True,
    delivery_enabled: bool = True,
    bindings: Bindings | None = None,
    backend: FakeScheduler | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[IntelligenceScheduler, FakeScheduler, Analysis, Reports, Alerts, Delivery]:
    actual_backend = backend or FakeScheduler(timezone="Asia/Shanghai")
    analysis = Analysis()
    reports = Reports()
    alerts = Alerts()
    delivery = Delivery()
    scheduler = IntelligenceScheduler(
        analysis=analysis,
        reports=reports,
        alerts=alerts,
        delivery=delivery,
        bindings=bindings or Bindings(),
        analysis_enabled=analysis_enabled,
        alerts_enabled=alerts_enabled,
        daily_enabled=daily_enabled,
        delivery_enabled=delivery_enabled,
        daily_hour=9,
        timezone="Asia/Shanghai",
        scheduler=actual_backend,
        clock=clock or (lambda: datetime(2026, 7, 22, 0, tzinfo=UTC)),
    )
    return scheduler, actual_backend, analysis, reports, alerts, delivery


def test_registers_enabled_jobs_with_stable_ids_and_non_reentrant_schedules() -> None:
    scheduler, backend, *_ = build_scheduler()

    scheduler.start()
    scheduler.start()

    assert backend.starts == 1
    assert set(backend.jobs) == {
        ANALYSIS_JOB_ID,
        DELIVERY_JOB_ID,
        DAILY_PREPARE_JOB_ID,
        DAILY_JOB_ID,
    }
    _, analysis_options = backend.jobs[ANALYSIS_JOB_ID]
    assert {
        key: analysis_options[key]
        for key in ("trigger", "minutes", "max_instances", "coalesce", "replace_existing")
    } == {
        "trigger": "interval",
        "minutes": 5,
        "max_instances": 1,
        "coalesce": True,
        "replace_existing": True,
    }
    _, delivery_options = backend.jobs[DELIVERY_JOB_ID]
    assert delivery_options["trigger"] == "interval"
    assert delivery_options["minutes"] == 1
    assert delivery_options["max_instances"] == 1
    _, daily_options = backend.jobs[DAILY_JOB_ID]
    assert daily_options["trigger"] == "cron"
    assert daily_options["hour"] == 9
    assert daily_options["minute"] == 0
    assert daily_options["max_instances"] == 1
    _, prepare_options = backend.jobs[DAILY_PREPARE_JOB_ID]
    assert prepare_options["trigger"] == "cron"
    assert prepare_options["hour"] == 8
    assert prepare_options["minute"] == 40
    assert backend.timezone == "Asia/Shanghai"


def test_registers_only_jobs_whose_capabilities_are_enabled() -> None:
    scheduler, backend, *_ = build_scheduler(
        analysis_enabled=True,
        alerts_enabled=False,
        daily_enabled=False,
        delivery_enabled=True,
    )

    scheduler.start()

    assert set(backend.jobs) == {ANALYSIS_JOB_ID, DELIVERY_JOB_ID}


async def test_analysis_queues_alerts_only_for_current_binding() -> None:
    scheduler, backend, analysis, _, alerts, _ = build_scheduler(bindings=Bindings("chat-one"))
    scheduler.start()

    callback, _ = backend.jobs[ANALYSIS_JOB_ID]
    await callback()

    assert analysis.calls == [10]
    assert alerts.calls == [("chat-one", datetime(2026, 7, 22, 0, tzinfo=UTC))]


async def test_active_binding_absence_safely_skips_alerts_and_daily_report() -> None:
    scheduler, backend, analysis, reports, alerts, _ = build_scheduler(bindings=Bindings(None))
    scheduler.start()

    await backend.jobs[ANALYSIS_JOB_ID][0]()
    await backend.jobs[DAILY_PREPARE_JOB_ID][0]()
    await backend.jobs[DAILY_JOB_ID][0]()

    assert analysis.calls == [10]
    assert alerts.calls == []
    assert reports.preview_calls == []
    assert reports.queue_calls == []


async def test_daily_prepare_and_send_are_separate_jobs() -> None:
    scheduler, backend, _, reports, _, _ = build_scheduler()
    scheduler.start()

    await backend.jobs[DAILY_PREPARE_JOB_ID][0]()
    await backend.jobs[DAILY_JOB_ID][0]()
    await backend.jobs[DAILY_JOB_ID][0]()

    report_date = datetime(2026, 7, 22, 0, tzinfo=UTC).date()
    assert reports.preview_calls == [("chat-one", report_date)]
    assert reports.queue_calls == [
        ("chat-one", report_date),
        ("chat-one", report_date),
    ]


async def test_starting_after_daily_hour_registers_and_runs_idempotent_catchup() -> None:
    now = datetime(2026, 7, 22, 2, 5, tzinfo=UTC)
    scheduler, backend, _, reports, _, _ = build_scheduler(clock=lambda: now)

    scheduler.start()

    callback, options = backend.jobs[DAILY_CATCHUP_JOB_ID]
    assert options["trigger"] == "date"
    assert options["run_date"] == now

    await callback()

    assert reports.preview_calls == [("chat-one", now.date())]
    assert reports.queue_calls == [("chat-one", now.date())]


async def test_already_sent_daily_report_is_an_idempotent_scheduler_noop(caplog) -> None:
    class AlreadySentReports(Reports):
        async def queue_previewed(self, group_id: str, report_date: object) -> int:
            del group_id, report_date
            raise ReportAlreadySent("private group and report details")

    scheduler, backend, *_ = build_scheduler()
    scheduler._reports = AlreadySentReports()
    scheduler.start()

    with caplog.at_level(logging.INFO):
        await backend.jobs[DAILY_JOB_ID][0]()

    assert "daily report already sent; skipping" in caplog.text
    assert "intelligence daily job failed" not in caplog.text
    assert "private" not in caplog.text


async def test_jobs_contain_failures_without_logging_sensitive_details(
    caplog,
) -> None:
    secret = "sensitive article body and prompt"

    class FailingAnalysis(Analysis):
        async def drain(self, *, limit: int) -> None:
            raise RuntimeError(secret)

    scheduler, backend, *_ = build_scheduler()
    scheduler._analysis = FailingAnalysis()
    scheduler.start()

    with caplog.at_level(logging.ERROR):
        await backend.jobs[ANALYSIS_JOB_ID][0]()

    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text


async def test_shutdown_cancels_and_awaits_running_job() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingAnalysis(Analysis):
        async def drain(self, *, limit: int) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    scheduler, backend, *_ = build_scheduler()
    scheduler._analysis = BlockingAnalysis()
    scheduler.start()
    task = asyncio.create_task(backend.jobs[ANALYSIS_JOB_ID][0]())
    await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.wait_for(scheduler.aclose(), timeout=1)

    assert cancelled.is_set()
    assert task.done()
    assert backend.shutdowns == [True]
