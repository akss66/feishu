from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from commerce_agent.ingestion.models import Trigger
from commerce_agent.ingestion.scheduler import INGESTION_JOB_ID, IngestionScheduler


class FakeAsyncIOScheduler:
    def __init__(self, *, timezone: str) -> None:
        self.timezone = timezone
        self.jobs: dict[str, tuple[Callable[..., Any], dict[str, Any]]] = {}
        self.start_calls = 0
        self.shutdown_calls: list[bool] = []

    def add_job(self, callback: Callable[..., Any], **kwargs: Any) -> object:
        self.jobs[kwargs["id"]] = (callback, kwargs)
        return object()

    def start(self) -> None:
        self.start_calls += 1

    def shutdown(self, *, wait: bool) -> None:
        self.shutdown_calls.append(wait)


class RecordingService:
    def __init__(self) -> None:
        self.triggers: list[Trigger] = []

    async def run_all(self, trigger: Trigger) -> tuple[object, ...]:
        self.triggers.append(trigger)
        return ()


async def test_registers_one_120_minute_job_in_configured_timezone() -> None:
    backend = FakeAsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler = IngestionScheduler(
        RecordingService(),
        interval_minutes=120,
        timezone="Asia/Shanghai",
        scheduler=backend,
    )

    scheduler.start()
    scheduler.start()

    assert backend.timezone == "Asia/Shanghai"
    assert backend.start_calls == 1
    assert list(backend.jobs) == [INGESTION_JOB_ID]
    _, options = backend.jobs[INGESTION_JOB_ID]
    assert options["trigger"] == "interval"
    assert options["minutes"] == 120
    assert options["max_instances"] == 1
    assert options["replace_existing"] is True
    assert "next_run_time" not in options


async def test_registered_job_awaits_ingestion_with_scheduled_trigger() -> None:
    backend = FakeAsyncIOScheduler(timezone="UTC")
    service = RecordingService()
    scheduler = IngestionScheduler(service, scheduler=backend)
    scheduler.start()
    callback, _ = backend.jobs[INGESTION_JOB_ID]

    await callback()

    assert service.triggers == [Trigger.SCHEDULED]


async def test_ingestion_exception_is_contained_for_future_schedules() -> None:
    class FailingService:
        async def run_all(self, trigger: Trigger) -> tuple[object, ...]:
            del trigger
            raise RuntimeError("collector failed")

    backend = FakeAsyncIOScheduler(timezone="UTC")
    scheduler = IngestionScheduler(FailingService(), scheduler=backend)
    scheduler.start()
    callback, _ = backend.jobs[INGESTION_JOB_ID]

    await callback()


async def test_shutdown_is_graceful_and_idempotent() -> None:
    backend = FakeAsyncIOScheduler(timezone="UTC")
    scheduler = IngestionScheduler(RecordingService(), scheduler=backend)
    scheduler.start()

    await scheduler.aclose()
    await scheduler.aclose()

    assert backend.shutdown_calls == [True]


async def test_shutdown_cancels_and_awaits_a_running_ingestion() -> None:
    class BlockingService:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.finished = asyncio.Event()

        async def run_all(self, trigger: Trigger) -> tuple[object, ...]:
            del trigger
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.finished.set()

    class RunningBackend(FakeAsyncIOScheduler):
        def start(self) -> None:
            super().start()
            callback, _ = self.jobs[INGESTION_JOB_ID]
            self.task = asyncio.create_task(callback())

    service = BlockingService()
    backend = RunningBackend(timezone="UTC")
    scheduler = IngestionScheduler(service, scheduler=backend)
    scheduler.start()
    await asyncio.wait_for(service.started.wait(), timeout=1)

    try:
        await scheduler.aclose()
        assert service.finished.is_set()
        assert backend.task.done()
    finally:
        if not backend.task.done():
            backend.task.cancel()
            await asyncio.gather(backend.task, return_exceptions=True)


async def test_real_backend_is_stopped_when_aclose_returns() -> None:
    backend = AsyncIOScheduler(timezone="UTC")
    scheduler = IngestionScheduler(RecordingService(), scheduler=backend)
    scheduler.start()

    await scheduler.aclose()

    assert backend.running is False


async def test_submitted_job_cannot_outlive_scheduler_shutdown() -> None:
    class ResourceService:
        def __init__(self) -> None:
            self.resource_closed = False
            self.accessed_after_close = False

        async def run_all(self, trigger: Trigger) -> tuple[object, ...]:
            del trigger
            if self.resource_closed:
                self.accessed_after_close = True
            try:
                await asyncio.Event().wait()
            finally:
                if self.resource_closed:
                    self.accessed_after_close = True

    service = ResourceService()
    backend = AsyncIOScheduler(timezone="UTC")
    scheduler = IngestionScheduler(service, scheduler=backend)
    scheduler.start()
    backend.modify_job(INGESTION_JOB_ID, next_run_time=datetime.now(UTC))
    backend._process_jobs()
    executor = backend._executors["default"]
    submitted = tuple(executor._pending_futures)
    assert submitted
    assert not scheduler._running_tasks

    await scheduler.aclose()
    service.resource_closed = True

    assert all(task.done() for task in submitted)
    assert service.accessed_after_close is False
