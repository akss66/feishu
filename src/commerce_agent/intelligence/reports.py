from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from commerce_agent.ingestion.models import Platform, TrustTier
from commerce_agent.intelligence.models import RiskLevel, RiskProfile, ScoredAnalysis

if TYPE_CHECKING:
    from commerce_agent.intelligence.repository import SqlAlchemyIntelligenceRepository
    from commerce_agent.persistence.intelligence_preferences import (
        SqlAlchemyIntelligencePreferenceStore,
    )

_RISK_ORDER = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_PROFILE_LABELS = {
    RiskProfile.CONSERVATIVE: "保守",
    RiskProfile.DEFAULT: "默认",
    RiskProfile.AGGRESSIVE: "激进",
}
_CONSERVATIVE_ACTION = "人工复核原文和适用范围后再决定业务变更"
_REVERSIBLE_ACTION = "指定负责人核对原文；准备影响清单，不执行不可逆操作"


@dataclass(frozen=True, slots=True)
class CoverageRow:
    platform: Platform
    enabled_source_count: int
    verified_update_count: int


@dataclass(frozen=True, slots=True)
class DailyReportDraft:
    report_date: date
    window_start: datetime
    window_end: datetime
    selected_analysis_ids: tuple[int, ...]
    payload: dict[str, Any]


class ReportAlreadySent(RuntimeError):
    pass


def report_window(report_date: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    end_local = datetime.combine(report_date, time(hour=9), tzinfo=timezone)
    start_local = end_local - timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def rank_key(item: ScoredAnalysis) -> tuple[int, int, int, datetime, int]:
    return (
        _RISK_ORDER[item.resolution.risk_level],
        item.evidence_confidence,
        int(item.candidate.trust_tier is TrustTier.OFFICIAL),
        item.candidate.fetched_at,
        item.analysis_id,
    )


def _preferred_event_item(items: list[ScoredAnalysis]) -> ScoredAnalysis:
    official = [item for item in items if item.candidate.trust_tier is TrustTier.OFFICIAL]
    return max(official or items, key=rank_key)


class DailyReportComposer:
    def __init__(self, timezone: ZoneInfo = _SHANGHAI) -> None:
        self._timezone = timezone

    def compose(
        self,
        *,
        report_date: date,
        analyses: tuple[ScoredAnalysis, ...],
        coverage: tuple[CoverageRow, ...] = (),
        profile: RiskProfile = RiskProfile.DEFAULT,
    ) -> DailyReportDraft:
        by_event: dict[str, list[ScoredAnalysis]] = {}
        for item in analyses:
            if item.evidence_confidence >= 60:
                by_event.setdefault(item.event_fingerprint, []).append(item)
        representatives = tuple(_preferred_event_item(items) for items in by_event.values())
        selected = tuple(sorted(representatives, key=rank_key, reverse=True)[:15])
        payload = (
            build_b_payload(report_date, selected, coverage, profile)
            if selected
            else build_health_payload(report_date, coverage, profile)
        )
        window_start, window_end = report_window(report_date, self._timezone)
        return DailyReportDraft(
            report_date=report_date,
            window_start=window_start,
            window_end=window_end,
            selected_analysis_ids=tuple(item.analysis_id for item in selected),
            payload=payload,
        )


def _title(report_date: date, profile: RiskProfile) -> str:
    return (
        f"跨境电商每日情报 · {report_date.isoformat()} "
        f"· 策略：{_PROFILE_LABELS[profile]}"
    )


def _coverage_line(row: CoverageRow) -> str:
    if not row.enabled_source_count:
        return f"{row.platform.value}：该平台尚无合规启用来源"
    if not row.verified_update_count:
        return f"{row.platform.value}：无已验证更新"
    return f"{row.platform.value}：已验证 {row.verified_update_count} 条"


def _coverage_lines(coverage: tuple[CoverageRow, ...]) -> list[str]:
    return [_coverage_line(row) for row in coverage]


def build_health_payload(
    report_date: date,
    coverage: tuple[CoverageRow, ...],
    profile: RiskProfile,
) -> dict[str, Any]:
    return {
        "title": _title(report_date, profile),
        "theme": "blue",
        "risk_profile": profile.value,
        "sections": [
            {"title": "AI 今日提炼", "items": ["本窗口无已验证更新。"]},
            {"title": "数据覆盖与来源", "items": _coverage_lines(coverage)},
        ],
    }


def _verified_risk_line(item: ScoredAnalysis, profile: RiskProfile) -> str:
    actions = (
        (_CONSERVATIVE_ACTION,)
        if profile is RiskProfile.CONSERVATIVE
        else tuple(action.action for action in item.result.action_items)
    )
    suffix = f"｜{'；'.join(actions)}" if actions else ""
    return f"{item.resolution.risk_level.value}｜{item.result.impact}{suffix}"


def build_b_payload(
    report_date: date,
    selected: tuple[ScoredAnalysis, ...],
    coverage: tuple[CoverageRow, ...],
    profile: RiskProfile,
) -> dict[str, Any]:
    verified = tuple(item for item in selected if item.evidence_confidence >= 75)
    pending = tuple(item for item in selected if 60 <= item.evidence_confidence < 75)
    platforms: dict[str, list[str]] = {}
    for item in verified:
        for platform in item.result.platforms:
            platforms.setdefault(platform.value, []).append(
                f"{item.result.headline_zh}（可信度 {item.evidence_confidence}）"
            )

    pending_lines = [
        (
            f"早期信号·待核实｜{item.result.headline_zh}｜{_REVERSIBLE_ACTION}"
            if profile is RiskProfile.AGGRESSIVE
            else f"待核实｜{item.result.headline_zh}"
        )
        for item in pending
    ]
    risk_items = [
        _verified_risk_line(item, profile)
        for item in verified
        if item.resolution.risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH}
    ] + pending_lines

    if profile is RiskProfile.CONSERVATIVE:
        recommended_actions = [_CONSERVATIVE_ACTION]
    else:
        recommended_actions = [
            action.action for item in verified for action in item.result.action_items
        ]
        if profile is RiskProfile.AGGRESSIVE and pending:
            recommended_actions.append(_REVERSIBLE_ACTION)

    return {
        "title": _title(report_date, profile),
        "theme": "blue",
        "risk_profile": profile.value,
        "sections": [
            {
                "title": "AI 今日提炼",
                "items": [item.result.summary_zh for item in verified]
                or ["本窗口无已验证更新。"],
            },
            {"title": "风险与待办", "items": risk_items},
            {
                "title": "平台动态",
                "items": [
                    f"{platform}：{'；'.join(items)}" for platform, items in platforms.items()
                ],
            },
            {"title": "今日建议", "items": recommended_actions},
            {
                "title": "数据覆盖与来源",
                "items": _coverage_lines(coverage)
                + [
                    f"{item.candidate.source_name}｜{item.candidate.canonical_url}"
                    for item in selected
                ],
            },
        ],
    }


class DailyReportService:
    def __init__(
        self,
        repository: SqlAlchemyIntelligenceRepository,
        composer: DailyReportComposer,
        preferences: SqlAlchemyIntelligencePreferenceStore,
        *,
        timezone: ZoneInfo,
        default_profile: RiskProfile = RiskProfile.DEFAULT,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._composer = composer
        self._preferences = preferences
        self._timezone = timezone
        self._default_profile = default_profile
        self._clock = clock

    async def preview(self, group_id: str, report_date: date) -> DailyReportDraft:
        start, end = report_window(report_date, self._timezone)
        analyses = await self._repository.list_report_analyses(
            window_start=start, window_end=end
        )
        coverage = await self._repository.list_coverage(window_start=start, window_end=end)
        profile = await self._preferences.get(group_id, default=self._default_profile)
        draft = self._composer.compose(
            report_date=report_date,
            analyses=analyses,
            coverage=coverage,
            profile=profile,
        )
        report_id = await self._repository.save_report(group_id, draft, now=self._clock())
        await self._repository.mark_report_previewed(report_id)
        return draft

    async def queue_previewed(self, group_id: str, report_date: date) -> int:
        report_id = await self._repository.get_report_id(group_id, report_date)
        return await self._repository.queue_report(report_id, now=self._clock())

    async def generate_and_queue(self, group_id: str, report_date: date) -> int:
        await self.preview(group_id, report_date)
        return await self.queue_previewed(group_id, report_date)
