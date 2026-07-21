from datetime import UTC, datetime

import pytest

from commerce_agent.ingestion.models import Platform
from commerce_agent.intelligence.models import (
    ActionItem,
    AlertQualification,
    AnalysisResult,
    EventType,
    EvidenceClaim,
    RiskLevel,
    RiskProfile,
)
from commerce_agent.intelligence.risk import RiskPolicy, event_fingerprint


@pytest.fixture
def result() -> AnalysisResult:
    return AnalysisResult(
        headline_zh="Amazon 全球卖家费用政策更新",
        summary_zh=(
            "Amazon 发布新的卖家费用政策，明确说明费用调整、生效日期以及适用区域。"
            "卖家需要复核商品成本、定价和活动预算，并在规则生效前完成必要准备。"
            "相关团队应持续核对官方原文，确认账户范围并记录仍待确认的实施细节。"
        ),
        event_type=EventType.MARKET_UPDATE,
        platforms=(Platform.AMAZON,),
        regions=("global",),
        affected_seller_types=("all",),
        effective_at=datetime(2026, 8, 1, tzinfo=UTC),
        risk_level=RiskLevel.MEDIUM,
        impact="费用上升可能影响商品毛利和定价策略。",
        rationale=(
            EvidenceClaim(
                claim="卖家费用将在生效日上调",
                quote="Seller fees will increase on the effective date.",
            ),
        ),
        action_items=(ActionItem(action="复核成本和定价", owner_type="运营"),),
        uncertainties=(),
        tags=("费用",),
    )


@pytest.mark.parametrize(
    ("profile", "score", "risk", "eligible", "qualification"),
    [
        (RiskProfile.CONSERVATIVE, 84, RiskLevel.HIGH, False, AlertQualification.NONE),
        (RiskProfile.CONSERVATIVE, 85, RiskLevel.HIGH, True, AlertQualification.VERIFIED),
        (RiskProfile.CONSERVATIVE, 100, RiskLevel.MEDIUM, False, AlertQualification.NONE),
        (RiskProfile.DEFAULT, 74, RiskLevel.HIGH, False, AlertQualification.NONE),
        (RiskProfile.DEFAULT, 75, RiskLevel.MEDIUM, True, AlertQualification.VERIFIED),
        (RiskProfile.AGGRESSIVE, 59, RiskLevel.HIGH, False, AlertQualification.NONE),
        (RiskProfile.AGGRESSIVE, 60, RiskLevel.HIGH, True, AlertQualification.EARLY_SIGNAL),
        (RiskProfile.AGGRESSIVE, 74, RiskLevel.MEDIUM, True, AlertQualification.EARLY_SIGNAL),
        (RiskProfile.AGGRESSIVE, 75, RiskLevel.HIGH, True, AlertQualification.VERIFIED),
        (RiskProfile.AGGRESSIVE, 100, RiskLevel.LOW, False, AlertQualification.NONE),
    ],
)
def test_alert_profile_boundaries(
    profile: RiskProfile,
    score: int,
    risk: RiskLevel,
    eligible: bool,
    qualification: AlertQualification,
    result: AnalysisResult,
) -> None:
    decision = RiskPolicy().assess(
        result.model_copy(update={"risk_level": risk}), score, profile
    )

    assert decision.eligible_for_alert is eligible
    assert decision.alert_qualification is qualification


def test_rule_floor_only_raises_model_risk(result: AnalysisResult) -> None:
    policy = RiskPolicy()

    medium = policy.resolve(
        result.model_copy(
            update={"event_type": EventType.FEES, "risk_level": RiskLevel.LOW}
        )
    )
    high = policy.resolve(
        result.model_copy(
            update={
                "event_type": EventType.ACCOUNT_ENFORCEMENT,
                "risk_level": RiskLevel.MEDIUM,
            }
        )
    )

    assert medium.risk_level is RiskLevel.MEDIUM
    assert medium.rule_hits == ("event_floor:medium",)
    assert medium.needs_review is False
    assert high.risk_level is RiskLevel.HIGH
    assert high.needs_review is False


def test_severe_rule_model_conflict_never_alerts_in_any_profile(
    result: AnalysisResult,
) -> None:
    conflicting = result.model_copy(
        update={"risk_level": RiskLevel.LOW, "event_type": EventType.ACCOUNT_ENFORCEMENT}
    )

    for profile in RiskProfile:
        decision = RiskPolicy().assess(conflicting, 100, profile)
        assert decision.needs_review is True
        assert decision.eligible_for_alert is False
        assert decision.alert_qualification is AlertQualification.NONE


def test_event_fingerprint_is_stable_for_normalized_subject_and_fact_order(
    result: AnalysisResult,
) -> None:
    reordered = result.model_copy(
        update={
            "platforms": (Platform.EBAY, Platform.AMAZON),
            "rationale": (
                EvidenceClaim(claim="Second FACT", quote="second quote"),
                EvidenceClaim(claim="卖家费用将在生效日上调", quote="first quote"),
            ),
        }
    )
    equivalent = reordered.model_copy(
        update={
            "platforms": (Platform.AMAZON, Platform.EBAY),
            "rationale": tuple(reversed(reordered.rationale)),
        }
    )

    assert event_fingerprint(reordered, subject="  Seller   Fees ") == event_fingerprint(
        equivalent, subject="seller fees"
    )
    assert event_fingerprint(reordered, subject="Seller Fees") != event_fingerprint(
        reordered, subject="Account enforcement"
    )
