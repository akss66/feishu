from dataclasses import replace
from datetime import UTC, datetime

from commerce_agent.ingestion.models import Platform, TrustTier
from commerce_agent.intelligence.evidence import EvidenceScorer
from commerce_agent.intelligence.models import (
    ActionItem,
    AnalysisCandidate,
    AnalysisResult,
    EventType,
    EvidenceClaim,
    RiskLevel,
)


def _candidate() -> AnalysisCandidate:
    quote = "Seller fees will increase on the effective date."
    return AnalysisCandidate(
        job_id=1,
        lease_token="lease-one",
        document_version_id=2,
        source_id="official-news",
        source_name="Official News",
        trust_tier=TrustTier.OFFICIAL,
        canonical_url="https://example.com/fees",
        content_hash="a" * 64,
        title="Seller fee update",
        body=quote + " " + ("Complete extracted article body. " * 20),
        language="en",
        language_confidence=0.99,
        author="Platform",
        published_at=datetime(2026, 7, 20, tzinfo=UTC),
        fetched_at=datetime(2026, 7, 21, tzinfo=UTC),
        platforms=(Platform.AMAZON,),
        regions=("global",),
    )


def _result() -> AnalysisResult:
    return AnalysisResult(
        headline_zh="Amazon 全球卖家费用政策更新",
        summary_zh=(
            "Amazon 发布新的卖家费用政策，明确说明费用调整、生效日期以及适用区域。"
            "卖家需要复核商品成本、定价和活动预算，并在规则生效前完成必要准备。"
            "相关团队应持续核对官方原文，确认账户范围并记录仍待确认的实施细节。"
        ),
        event_type=EventType.FEES,
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
        action_items=(
            ActionItem(action="复核成本和定价", owner_type="运营", deadline=None),
        ),
        uncertainties=(),
        tags=("费用",),
    )


def test_official_single_source_can_reach_90_but_not_cross_source_points() -> None:
    score = EvidenceScorer().score(_candidate(), _result(), corroborating_sources=1)

    assert score == 90


def test_all_six_score_components_are_deterministic() -> None:
    candidate = _candidate()
    result = _result()
    scorer = EvidenceScorer()

    assert scorer.score(candidate, result, corroborating_sources=2) == 100
    assert (
        scorer.score(
            replace(candidate, trust_tier=TrustTier.MEDIA),
            result,
            corroborating_sources=2,
        )
        == 90
    )
    assert (
        scorer.score(
            replace(candidate, body="Short body without the evidence quote."),
            result,
            corroborating_sources=2,
        )
        == 70
    )
    assert (
        scorer.score(
            replace(candidate, body="Seller fees will increase on the effective date."),
            result.model_copy(update={"effective_at": None}),
            corroborating_sources=1,
        )
        == 80
    )


def test_single_media_publisher_is_capped_at_seventy() -> None:
    candidate = replace(
        _candidate(),
        trust_tier=TrustTier.MEDIA,
        publisher_key="reuters.com",
        attribution="Reuters",
        content_scope="metadata_only",
    )

    assert EvidenceScorer().score(candidate, _result(), corroborating_sources=1) == 70


def test_two_media_publishers_remove_the_single_publisher_cap() -> None:
    candidate = replace(
        _candidate(),
        trust_tier=TrustTier.MEDIA,
        publisher_key="reuters.com",
        attribution="Reuters",
        content_scope="metadata_only",
    )

    assert EvidenceScorer().score(candidate, _result(), corroborating_sources=2) == 90
