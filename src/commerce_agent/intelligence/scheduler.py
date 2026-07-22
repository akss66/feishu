"""Safe periodic scheduling for intelligence analysis and delivery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_SCHEDULER_SHUTDOWN
from apscheduler.schedulers.asyncio import AsyncIOScheduler

ANALYSIS_JOB_ID = "intelligence-analysis-drain"
DELIVERY_JOB_ID = "intelligence-delivery-retry"
DAILY_JOB_ID = "intelligence-daily-report"

_LOGGER = logging.getLogger(__name__)


class _Analysis(Protocol):
    async def drain(self, *, limit: int) -> Any: ...


class _Reports(Protocol):
    async def generate_and_queue(self, group_id: str, report_date: Any) -> int: ...


class _Alerts(Protocol):
    async def queue_due(self, group_id: str, *, now: datetime) -> tuple[int, ...]: ...


class _Delivery(Protocol):
    async def drain(self, *, limit: int) -> Any: ...


class _Bindings(Protocol):
    async def get_active_chat_id(self) -> str | None: ...


class IntelligenceScheduler:
    """Own the fixed intelligence jobs and contain failures at their boundary."""

    def __init__(
        self,
        *,
        analysis: _Analysis,
        reports: _Reports,
        alerts: _Alerts,
        delivery: _Delivery,
        bindings: _Bindings,
        analysis_enabled: bool,
        alerts_enabled: bool,
        daily_enabled: bool,
        delivery_enabled: bool,
        daily_hour: int,
        timezone: str = "Asia/Shanghai",
        scheduler: Any | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._analysis = analysis
        self._reports = reports
        self._alerts = alerts
        self._delivery = delivery
        self._bindings = bindings
        self._analysis_enabled = analysis_enabled
        self._alerts_enabled = alerts_enabled
        self._daily_enabled = daily_enabled
        self._delivery_enabled = delivery_enabled
        self._daily_hour = daily_hour
        self._timezone = ZoneInfo(timezone)
        self._scheduler = scheduler or AsyncIOScheduler(timezone=timezone)
        self._clock = clock
        self._started = False
        self._running_tasks: set[asyncio.Task[None]] = set()
        self._shutdown_complete = asyncio.Event()
        self._shutdown_loop: asyncio.AbstractEventLoop | None = None
        add_listener = getattr(self._scheduler, "add_listener", None)
        self._waits_for_shutdown_event = callable(add_listener)
        if self._waits_for_shutdown_event:
            add_listener(self._on_scheduler_shutdown, EVENT_SCHEDULER_SHUTDOWN)

    def start(self) -> None:
        if self._started:
            return
        self._shutdown_complete.clear()
        if self._analysis_enabled:
            self._add_job(
                self._run_analysis,
                trigger="interval",
                minutes=5,
                job_id=ANALYSIS_JOB_ID,
            )
        if self._delivery_enabled:
            self._add_job(
                self._run_delivery,
                trigger="interval",
                minutes=1,
                job_id=DELIVERY_JOB_ID,
            )
        if self._daily_enabled:
            self._add_job(
                self._run_daily,
                trigger="cron",
                hour=self._daily_hour,
                minute=0,
                job_id=DAILY_JOB_ID,
            )
        self._scheduler.start()
        self._started = True

    def _add_job(self, callback: Callable[[], Any], *, job_id: str, **schedule: Any) -> None:
        self._scheduler.add_job(
            callback,
            id=job_id,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
            **schedule,
        )

    async def _run_analysis(self) -> None:
        task = self._track_current_task()
        try:
            await self._analysis.drain(limit=10)
            if self._alerts_enabled:
                group_id = await self._bindings.get_active_chat_id()
                if group_id is not None:
                    await self._alerts.queue_due(group_id, now=self._clock())
        except Exception as error:
            _LOGGER.error(
                "intelligence analysis job failed (exception_type=%s)",
                type(error).__name__,
            )
        finally:
            self._untrack(task)

    async def _run_daily(self) -> None:
        task = self._track_current_task()
        try:
            group_id = await self._bindings.get_active_chat_id()
            if group_id is not None:
                report_date = self._clock().astimezone(self._timezone).date()
                await self._reports.generate_and_queue(group_id, report_date)
        except Exception as error:
            _LOGGER.error(
                "intelligence daily job failed (exception_type=%s)",
                type(error).__name__,
            )
        finally:
            self._untrack(task)

    async def _run_delivery(self) -> None:
        task = self._track_current_task()
        try:
            await self._delivery.drain(limit=20)
        except Exception as error:
            _LOGGER.error(
                "intelligence delivery job failed (exception_type=%s)",
                type(error).__name__,
            )
        finally:
            self._untrack(task)

    def _track_current_task(self) -> asyncio.Task[None] | None:
        task = asyncio.current_task()
        if task is not None:
            self._running_tasks.add(task)
        return task

    def _untrack(self, task: asyncio.Task[None] | None) -> None:
        if task is not None:
            self._running_tasks.discard(task)

    async def aclose(self) -> None:
        if not self._started:
            return
        self._started = False
        self._shutdown_loop = asyncio.get_running_loop()
        self._scheduler.shutdown(wait=True)
        if self._waits_for_shutdown_event:
            await self._shutdown_complete.wait()
        tasks = tuple(self._running_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _on_scheduler_shutdown(self, event: Any) -> None:
        del event
        if self._shutdown_loop is not None:
            self._shutdown_loop.call_soon_threadsafe(self._shutdown_complete.set)


__all__ = [
    "ANALYSIS_JOB_ID",
    "DAILY_JOB_ID",
    "DELIVERY_JOB_ID",
    "IntelligenceScheduler",
]
