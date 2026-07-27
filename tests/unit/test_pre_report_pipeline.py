from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from commerce_agent.ingestion.models import Trigger
from commerce_agent.ingestion.pre_report import PreReportPipeline


class Collector:
    def __init__(self) -> None:
        self.calls = []

    async def run_until(self, trigger: Trigger, *, deadline: datetime):
        self.calls.append((trigger, deadline))
        return ("slow-source",)


class Analysis:
    def __init__(self) -> None:
        self.claims = [2, 0]

    async def drain(self, *, limit: int):
        assert limit == 20
        return SimpleNamespace(claimed=self.claims.pop(0))


class Reports:
    def __init__(self) -> None:
        self.preview_calls = []

    async def preview(self, group_id: str, report_date: date):
        self.preview_calls.append((group_id, report_date))


async def test_prepare_records_slow_sources_drains_analysis_and_saves_preview() -> None:
    collector = Collector()
    reports = Reports()
    report_date = date(2026, 7, 28)
    pipeline = PreReportPipeline(
        collector,
        Analysis(),
        reports,
        timezone=ZoneInfo("Asia/Shanghai"),
        clock=lambda: datetime(2026, 7, 28, 0, 40, tzinfo=UTC),
    )

    result = await pipeline.prepare("chat-one", report_date)

    assert result.source_timeouts == ("slow-source",)
    assert result.analysis_claimed == 2
    assert result.report_prepared is True
    assert reports.preview_calls == [("chat-one", report_date)]
    assert collector.calls == [
        (
            Trigger.SCHEDULED,
            datetime(2026, 7, 28, 0, 55, tzinfo=UTC),
        )
    ]
