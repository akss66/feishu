from __future__ import annotations

from datetime import UTC, datetime

import pytest

from commerce_agent.ingestion.models import Platform, TrustTier
from commerce_agent.intelligence.models import (
    ActionItem,
    AlertQualification,
    AnalysisCandidate,
    AnalysisResult,
    DeliveryMessage,
    EventType,
    EvidenceClaim,
    MessageKind,
    RiskLevel,
    RiskProfile,
    RiskResolution,
    ScoredAnalysis,
)
from commerce_agent.intelligence.reports import AlertComposer
from commerce_agent.intelligence.risk import RiskPolicy

NOW = datetime(2026, 7, 21, 1, tzinfo=UTC)


def _analysis(
    analysis_id: int,
    *,
    score: int,
    risk: RiskLevel,
    fingerprint: str | None = None,
) -> ScoredAnalysis:
    candidate = AnalysisCandidate(
        job_id=analysis_id,
        lease_token=None,
        document_version_id=analysis_id,
        source_id=f"source-{analysis_id}",
        source_name=f"来源 {analysis_id}",
        trust_tier=TrustTier.OFFICIAL,
        canonical_url=f"https://example.com/{analysis_id}",
        content_hash=f"{analysis_id:064x}",
        title=f"Policy update {analysis_id}",
        body="Policy update source body",
        language="en",
        language_confidence=0.99,
        author=None,
        published_at=None,
        fetched_at=NOW,
        platforms=(Platform.EBAY,),
        regions=("global",),
    )
    result = AnalysisResult(
        headline_zh=f"eBay 政策更新 {analysis_id}",
        summary_zh=(
            "eBay 发布新的政策更新，卖家需要核对适用站点、商品类别、生效日期与账户范围，"
            "重新评估对定价、库存和运营流程的影响，并在采取业务动作前核实官方原文，"
            "同时将结论同步给负责人持续跟进。"
        ),
        event_type=EventType.MARKET_UPDATE,
        platforms=(Platform.EBAY,),
        regions=("global",),
        affected_seller_types=("all",),
        effective_at=None,
        risk_level=risk,
        impact="可能影响卖家的定价与运营安排",
        rationale=(EvidenceClaim(claim="政策发生变化", quote="policy changed"),),
        action_items=(
            ActionItem(action="立即下架全部商品", owner_type="运营", deadline=NOW),
        ),
        uncertainties=("适用站点仍待确认",),
        tags=("政策",),
    )
    return ScoredAnalysis(
        analysis_id=analysis_id,
        candidate=candidate,
        result=result,
        evidence_confidence=score,
        resolution=RiskResolution(risk_level=risk, rule_hits=(), needs_review=False),
        event_fingerprint=fingerprint or f"event-{analysis_id}",
    )


class _Preferences:
    def __init__(self, profile: RiskProfile) -> None:
        self.profile = profile

    async def get(self, group_id: str, *, default: RiskProfile) -> RiskProfile:
        del group_id, default
        return self.profile


class _Repository:
    def __init__(self) -> None:
        self.messages: tuple[DeliveryMessage, ...] = ()

    async def queue_alerts(
        self,
        messages: tuple[DeliveryMessage, ...],
        *,
        now: datetime,
        dedup_hours: int,
    ) -> tuple[int, ...]:
        del now
        assert dedup_hours == 24
        self.messages = messages
        return tuple(range(1, len(messages) + 1))

    async def list_unqueued_alert_candidates(
        self, *, since: datetime, until: datetime
    ) -> tuple[ScoredAnalysis, ...]:
        del since, until
        return ()


def _service(profile: RiskProfile) -> tuple[AlertComposer, _Repository]:
    repository = _Repository()
    return (
        AlertComposer(repository, _Preferences(profile), RiskPolicy()),
        repository,
    )


async def test_verified_high_is_individual_and_verified_medium_is_batched() -> None:
    service, repository = _service(RiskProfile.DEFAULT)

    ids = await service.queue_batch(
        "chat-one",
        (
            _analysis(1, score=90, risk=RiskLevel.HIGH),
            _analysis(2, score=85, risk=RiskLevel.HIGH),
            _analysis(3, score=80, risk=RiskLevel.MEDIUM),
            _analysis(4, score=75, risk=RiskLevel.MEDIUM),
        ),
        now=NOW,
    )

    assert len(ids) == 3
    assert [message.kind for message in repository.messages] == [
        MessageKind.HIGH_ALERT,
        MessageKind.HIGH_ALERT,
        MessageKind.MEDIUM_ALERT_BATCH,
    ]
    assert len(repository.messages[-1].payload["items"]) == 2


@pytest.mark.parametrize(
    ("profile", "score", "risk", "count", "title", "theme"),
    [
        (RiskProfile.CONSERVATIVE, 85, RiskLevel.HIGH, 1, "高风险预警", "red"),
        (RiskProfile.CONSERVATIVE, 100, RiskLevel.MEDIUM, 0, None, None),
        (RiskProfile.DEFAULT, 75, RiskLevel.MEDIUM, 1, "中风险预警汇总", "orange"),
        (RiskProfile.AGGRESSIVE, 60, RiskLevel.HIGH, 1, "早期信号·待核实", "orange"),
        (RiskProfile.AGGRESSIVE, 75, RiskLevel.HIGH, 1, "高风险预警", "red"),
        (RiskProfile.AGGRESSIVE, 100, RiskLevel.LOW, 0, None, None),
    ],
)
async def test_alert_composition_uses_current_group_profile(
    profile: RiskProfile,
    score: int,
    risk: RiskLevel,
    count: int,
    title: str | None,
    theme: str | None,
) -> None:
    service, repository = _service(profile)

    ids = await service.queue_batch(
        "chat-one", (_analysis(1, score=score, risk=risk),), now=NOW
    )

    assert len(ids) == count
    if ids:
        payload = repository.messages[0].payload
        assert payload["title"] == title
        assert payload["theme"] == theme
        assert payload["items"][0]["risk_profile"] == profile.value


async def test_conservative_alert_replaces_model_action_with_fixed_verification() -> None:
    service, repository = _service(RiskProfile.CONSERVATIVE)

    await service.queue_batch(
        "chat-one", (_analysis(1, score=85, risk=RiskLevel.HIGH),), now=NOW
    )

    item = repository.messages[0].payload["items"][0]
    assert item["verification_status"] == AlertQualification.VERIFIED.value
    assert item["actions"] == [
        {
            "action": "人工复核原文和适用范围后再决定业务变更",
            "owner_type": "合规负责人",
            "deadline": None,
        }
    ]
    assert "立即下架全部商品" not in str(item)

async def test_aggressive_early_signal_has_only_fixed_reversible_actions() -> None:
    service, repository = _service(RiskProfile.AGGRESSIVE)

    await service.queue_batch(
        "chat-one", (_analysis(1, score=60, risk=RiskLevel.HIGH),), now=NOW
    )

    item = repository.messages[0].payload["items"][0]
    assert item["verification_status"] == AlertQualification.EARLY_SIGNAL.value
    assert item["actions"] == [
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
    ]
    assert "立即下架全部商品" not in str(item)
