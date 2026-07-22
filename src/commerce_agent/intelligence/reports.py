from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from commerce_agent.ingestion.models import Platform, TrustTier
from commerce_agent.intelligence.models import (
    AlertQualification,
    DeliveryMessage,
    MessageKind,
    RiskDecision,
    RiskLevel,
    RiskProfile,
    ScoredAnalysis,
)

if TYPE_CHECKING:
    from commerce_agent.intelligence.repository import SqlAlchemyIntelligenceRepository
    from commerce_agent.intelligence.risk import RiskPolicy
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
_CONSERVATIVE_ALERT_ACTION = {
    "action": "人工复核原文和适用范围后再决定业务变更",
    "owner_type": "合规负责人",
    "deadline": None,
}
_EARLY_SIGNAL_ACTIONS = (
    {
        "action": "指定负责人核对原文、适用范围与生效时间",
        "owner_type": "运营负责人",
        "deadline": None,
    },
    {
        "action": "准备可逆的影响清单，不执行下架、改价等不可逆操作",
        "owner_type": "业务负责人",
        "deadline": None,
    },
)


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


def _daily_actions(
    item: ScoredAnalysis,
    profile: RiskProfile,
    *,
    verified: bool,
) -> list[dict[str, object]]:
    if profile is RiskProfile.CONSERVATIVE:
        return [dict(_CONSERVATIVE_ALERT_ACTION)]
    if not verified:
        count = 2 if profile is RiskProfile.AGGRESSIVE else 1
        return [dict(action) for action in _EARLY_SIGNAL_ACTIONS[:count]]
    actions = [action.model_dump(mode="json") for action in item.result.action_items]
    return actions or [dict(_EARLY_SIGNAL_ACTIONS[0])]


def _daily_item(item: ScoredAnalysis, profile: RiskProfile) -> dict[str, object]:
    verified = item.evidence_confidence >= 75
    return {
        "analysis_id": item.analysis_id,
        "document_version_id": item.candidate.document_version_id,
        "content_hash": item.candidate.content_hash,
        "event_fingerprint": item.event_fingerprint,
        "risk_level": item.resolution.risk_level.value,
        "evidence_confidence": item.evidence_confidence,
        "risk_profile": profile.value,
        "verification_status": "verified" if verified else "early_signal",
        "headline": item.result.headline_zh,
        "summary": item.result.summary_zh,
        "impact": item.result.impact,
        "rationale": [claim.model_dump(mode="json") for claim in item.result.rationale],
        "actions": _daily_actions(item, profile, verified=verified),
        "uncertainties": list(item.result.uncertainties),
        "source_name": item.candidate.attribution or item.candidate.source_name,
        "source_url": item.candidate.canonical_url,
        "publisher_key": item.candidate.publisher_key,
    }


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
        "items": [_daily_item(item, profile) for item in selected],
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


def _alert_actions(
    item: ScoredAnalysis, decision: RiskDecision
) -> list[dict[str, object]]:
    if decision.alert_qualification is AlertQualification.EARLY_SIGNAL:
        return [dict(action) for action in _EARLY_SIGNAL_ACTIONS]
    if decision.profile is RiskProfile.CONSERVATIVE:
        return [dict(_CONSERVATIVE_ALERT_ACTION)]
    return [action.model_dump(mode="json") for action in item.result.action_items]


def alert_item(item: ScoredAnalysis, decision: RiskDecision) -> dict[str, object]:
    return {
        "analysis_id": item.analysis_id,
        "document_version_id": item.candidate.document_version_id,
        "content_hash": item.candidate.content_hash,
        "event_fingerprint": item.event_fingerprint,
        "risk_level": decision.risk_level.value,
        "evidence_confidence": item.evidence_confidence,
        "risk_profile": decision.profile.value,
        "verification_status": decision.alert_qualification.value,
        "headline": item.result.headline_zh,
        "summary": item.result.summary_zh,
        "impact": item.result.impact,
        "rationale": [claim.model_dump(mode="json") for claim in item.result.rationale],
        "actions": _alert_actions(item, decision),
        "uncertainties": list(item.result.uncertainties),
        "source_name": item.candidate.source_name,
        "source_url": item.candidate.canonical_url,
    }


def high_alert_message(
    group_id: str,
    item: ScoredAnalysis,
    decision: RiskDecision,
    now: datetime,
) -> DeliveryMessage:
    bucket = int(now.timestamp() // timedelta(days=1).total_seconds())
    return DeliveryMessage(
        idempotency_key=(
            f"alert:{group_id}:{item.event_fingerprint}:high:"
            f"{item.candidate.content_hash[:16]}:{bucket}"
        ),
        group_id=group_id,
        kind=MessageKind.HIGH_ALERT,
        payload={
            "title": "高风险预警",
            "theme": "red",
            "items": [alert_item(item, decision)],
        },
    )


def medium_alert_message(
    group_id: str,
    items: tuple[tuple[ScoredAnalysis, RiskDecision], ...],
    now: datetime,
) -> DeliveryMessage:
    bucket = int(now.timestamp() // timedelta(days=1).total_seconds())
    identities = "|".join(
        sorted(
            f"{item.event_fingerprint}:{decision.risk_level.value}:"
            f"{item.candidate.content_hash}"
            for item, decision in items
        )
    )
    digest = hashlib.sha256(identities.encode("utf-8")).hexdigest()
    early = any(
        decision.alert_qualification is AlertQualification.EARLY_SIGNAL
        for _, decision in items
    )
    return DeliveryMessage(
        idempotency_key=f"alert-batch:{group_id}:{digest}:{bucket}",
        group_id=group_id,
        kind=MessageKind.MEDIUM_ALERT_BATCH,
        payload={
            "title": "早期信号·待核实" if early else "中风险预警汇总",
            "theme": "orange",
            "items": [alert_item(item, decision) for item, decision in items],
        },
    )


class AlertComposer:
    def __init__(
        self,
        repository: SqlAlchemyIntelligenceRepository,
        preferences: SqlAlchemyIntelligencePreferenceStore,
        policy: RiskPolicy,
        *,
        default_profile: RiskProfile = RiskProfile.DEFAULT,
    ) -> None:
        self._repository = repository
        self._preferences = preferences
        self._policy = policy
        self._default_profile = default_profile

    async def queue_batch(
        self,
        group_id: str,
        analyses: tuple[ScoredAnalysis, ...],
        *,
        now: datetime,
    ) -> tuple[int, ...]:
        messages = await self._compose_messages(group_id, analyses, now=now)
        return await self._repository.queue_alerts(messages, now=now, dedup_hours=24)

    async def preview_batch(
        self,
        group_id: str,
        analyses: tuple[ScoredAnalysis, ...],
        *,
        now: datetime,
    ) -> tuple[DeliveryMessage, ...]:
        messages = await self._compose_messages(group_id, analyses, now=now)
        return await self._repository.preview_alerts(messages, now=now, dedup_hours=24)

    async def _compose_messages(
        self,
        group_id: str,
        analyses: tuple[ScoredAnalysis, ...],
        *,
        now: datetime,
    ) -> tuple[DeliveryMessage, ...]:
        profile = await self._preferences.get(group_id, default=self._default_profile)
        evaluated = tuple(
            (item, self._policy.assess(item.result, item.evidence_confidence, profile))
            for item in analyses
        )
        eligible = tuple(pair for pair in evaluated if pair[1].eligible_for_alert)
        verified_highs = tuple(
            pair
            for pair in eligible
            if pair[1].risk_level is RiskLevel.HIGH
            and pair[1].alert_qualification is AlertQualification.VERIFIED
        )
        orange = tuple(pair for pair in eligible if pair not in verified_highs)
        messages = tuple(
            [
                high_alert_message(group_id, item, decision, now)
                for item, decision in verified_highs
            ]
            + ([medium_alert_message(group_id, orange, now)] if orange else [])
        )
        return messages

    async def queue_due(self, group_id: str, *, now: datetime) -> tuple[int, ...]:
        analyses = await self._repository.list_unqueued_alert_candidates(
            since=now - timedelta(hours=24), until=now
        )
        return await self.queue_batch(group_id, analyses, now=now)
