"""Periodic public-source ingestion on the runtime asyncio loop."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from apscheduler.events import EVENT_SCHEDULER_SHUTDOWN
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from commerce_agent.ingestion.models import Trigger

INGESTION_JOB_ID = "public-source-ingestion"
RETENTION_JOB_ID = "temporary-media-retention"
_LOGGER = logging.getLogger(__name__)


class _IngestionService(Protocol):
    async def run_all(self, trigger: Trigger) -> tuple[Any, ...]: ...

    async def run_retention(self) -> int: ...


class IngestionScheduler:
    """Own one non-immediate interval job for the shared ingestion service."""

    def __init__(
        self,
        service: _IngestionService,
        *,
        interval_minutes: int = 120,
        retention_enabled: bool = True,
        timezone: str = "UTC",
        scheduler: AsyncIOScheduler | Any | None = None,
    ) -> None:
        self._service = service
        self._interval_minutes = interval_minutes
        self._retention_enabled = retention_enabled
        self._scheduler = scheduler or AsyncIOScheduler(timezone=timezone)
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
        if self._retention_enabled:
            self._scheduler.add_job(
                self._run_retention,
                trigger="interval",
                minutes=60,
                id=RETENTION_JOB_ID,
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
        self._scheduler.add_job(
            self._run,
            trigger="interval",
            minutes=self._interval_minutes,
            id=INGESTION_JOB_ID,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        self._scheduler.start()
        self._started = True

    @property
    def service(self) -> _IngestionService:
        return self._service

    async def _run(self) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._running_tasks.add(task)
        try:
            await self._service.run_all(Trigger.SCHEDULED)
        except Exception as error:
            _LOGGER.error(
                "scheduled ingestion failed (exception_type=%s)",
                type(error).__name__,
            )
        finally:
            if task is not None:
                self._running_tasks.discard(task)

    async def _run_retention(self) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._running_tasks.add(task)
        try:
            await self._service.run_retention()
        except Exception as error:
            _LOGGER.error(
                "scheduled temporary-media retention failed (exception_type=%s)",
                type(error).__name__,
            )
        finally:
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
