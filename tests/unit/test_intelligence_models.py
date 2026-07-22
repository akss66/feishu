from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from commerce_agent.ingestion.models import Platform
from commerce_agent.intelligence.models import (
    ActionItem,
    AnalysisResult,
    EventType,
    EvidenceClaim,
    RiskLevel,
)


def test_analysis_result_forbids_unknown_fields_and_short_summary() -> None:
    payload = {
        "headline_zh": "费用政策调整",
        "summary_zh": "过短",
        "event_type": EventType.FEES,
        "platforms": [Platform.EBAY],
        "regions": ["global"],
        "affected_seller_types": ["all"],
        "effective_at": None,
        "risk_level": RiskLevel.MEDIUM,
        "impact": "卖家成本可能上升",
        "rationale": [{"claim": "费用调整", "quote": "fees will change"}],
        "action_items": [{"action": "核对费率", "owner_type": "运营", "deadline": None}],
        "uncertainties": ["生效日期未知"],
        "tags": ["费用"],
        "unexpected": "rejected",
    }

    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)


def test_analysis_result_accepts_strict_valid_payload() -> None:
    result = AnalysisResult(
        headline_zh="eBay 全球费用政策更新",
        summary_zh=(
            "eBay 发布新的费用政策说明，卖家需要核对适用站点、商品类别、生效日期及账户范围，"
            "重新测算商品毛利和活动预算，并在调整价格或运营策略前逐项复核官方原文规则，"
            "同时将结论同步给财务和负责人，确保关键费用变更得到及时处理。"
        ),
        event_type=EventType.FEES,
        platforms=(Platform.EBAY,),
        regions=("global",),
        affected_seller_types=("all",),
        effective_at=datetime(2026, 7, 21, tzinfo=UTC),
        risk_level=RiskLevel.MEDIUM,
        impact="费用结构变化可能影响商品毛利",
        rationale=(EvidenceClaim(claim="费用发生变化", quote="fees will change"),),
        action_items=(ActionItem(action="复核成本表", owner_type="运营", deadline=None),),
        uncertainties=(),
        tags=("费用",),
    )

    assert result.platforms == (Platform.EBAY,)


def test_analysis_result_accepts_a_concise_grounded_summary() -> None:
    payload = {
        "headline_zh": "平台公告更新",
        "summary_zh": "平台发布变更公告，卖家需核对官方原文、适用范围与后续影响。",
        "event_type": EventType.POLICY,
        "platforms": [Platform.EBAY],
        "regions": ["global"],
        "affected_seller_types": [],
        "effective_at": None,
        "risk_level": RiskLevel.LOW,
        "impact": "具体影响有待核对。",
        "rationale": [{"claim": "公告已更新", "quote": "policy update"}],
        "action_items": [],
        "uncertainties": ["适用卖家范围未知"],
        "tags": ["公告"],
    }

    result = AnalysisResult.model_validate(payload)

    assert result.summary_zh == payload["summary_zh"]
