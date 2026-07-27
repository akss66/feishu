from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from commerce_agent.ingestion.models import Trigger


class PreReportCollector(Protocol):
    async def run_all(self, trigger: Trigger) -> tuple[Any, ...]: ...


class PreReportAnalysis(Protocol):
    async def drain(self, *, limit: int) -> Any: ...


class PreReportReports(Protocol):
    async def preview(self, group_id: str, report_date: date) -> Any: ...


@dataclass(frozen=True, slots=True)
class PreReportResult:
    report_date: date
    source_timeouts: tuple[str, ...]
    analysis_claimed: int
    report_prepared: bool


class PreReportPipeline:
    def __init__(
        self,
        collector: PreReportCollector | None,
        analysis: PreReportAnalysis,
        reports: PreReportReports,
        *,
        timezone: ZoneInfo,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._collector = collector
        self._analysis = analysis
        self._reports = reports
        self._timezone = timezone
        self._clock = clock

    async def prepare(self, group_id: str, report_date: date) -> PreReportResult:
        collect_deadline = datetime.combine(
            report_date,
            time(8, 55),
            tzinfo=self._timezone,
        ).astimezone(UTC)
        preview_deadline = datetime.combine(
            report_date,
            time(8, 59),
            tzinfo=self._timezone,
        ).astimezone(UTC)
        source_timeouts = await self._collect_until(collect_deadline)

        claimed = 0
        rounds = 0
        while self._clock() < preview_deadline and rounds < 100:
            batch = await self._analysis.drain(limit=20)
            batch_claimed = int(getattr(batch, "claimed", 0))
            claimed += batch_claimed
            rounds += 1
            if batch_claimed == 0:
                break
        await self._reports.preview(group_id, report_date)
        return PreReportResult(
            report_date=report_date,
            source_timeouts=source_timeouts,
            analysis_claimed=claimed,
            report_prepared=True,
        )

    async def _collect_until(self, deadline: datetime) -> tuple[str, ...]:
        if self._collector is None:
            return ()
        run_until = getattr(self._collector, "run_until", None)
        if callable(run_until):
            result = await run_until(Trigger.SCHEDULED, deadline=deadline)
            return tuple(result)
        timeout = max(0.0, (deadline - self._clock()).total_seconds())
        if timeout == 0:
            return ("public-source-ingestion",)
        try:
            await asyncio.wait_for(
                self._collector.run_all(Trigger.SCHEDULED),
                timeout=timeout,
            )
        except TimeoutError:
            return ("public-source-ingestion",)
        return ()
