"""Periodic public-source ingestion on the runtime asyncio loop."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from commerce_agent.ingestion.models import Trigger

INGESTION_JOB_ID = "public-source-ingestion"
_LOGGER = logging.getLogger(__name__)


class _IngestionService(Protocol):
    async def run_all(self, trigger: Trigger) -> tuple[Any, ...]: ...


class IngestionScheduler:
    """Own one non-immediate interval job for the shared ingestion service."""

    def __init__(
        self,
        service: _IngestionService,
        *,
        interval_minutes: int = 120,
        timezone: str = "UTC",
        scheduler: AsyncIOScheduler | Any | None = None,
    ) -> None:
        self._service = service
        self._interval_minutes = interval_minutes
        self._scheduler = scheduler or AsyncIOScheduler(timezone=timezone)
        self._started = False
        self._running_tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        if self._started:
            return
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

    async def aclose(self) -> None:
        if not self._started:
            return
        self._started = False
        self._scheduler.shutdown(wait=True)
        tasks = tuple(self._running_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
