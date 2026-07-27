from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from commerce_agent.ingestion.models import Platform, TrustTier
from commerce_agent.intelligence.models import (
    ActionItem,
    AnalysisCandidate,
    AnalysisResult,
    EventType,
    EvidenceClaim,
    RiskLevel,
    RiskProfile,
    RiskResolution,
    ScoredAnalysis,
)
from commerce_agent.intelligence.reports import (
    CoverageRow,
    DailyReportComposer,
    DailyReportService,
    report_window,
)


def _analysis(
    analysis_id: int,
    *,
    fingerprint: str | None = None,
    confidence: int = 90,
    risk: RiskLevel = RiskLevel.MEDIUM,
    trust_tier: TrustTier = TrustTier.MEDIA,
    fetched_at: datetime | None = None,
    model_action: str | None = None,
) -> ScoredAnalysis:
    action = model_action or f"模型建议 {analysis_id}"
    candidate = AnalysisCandidate(
        job_id=analysis_id,
        lease_token=None,
        document_version_id=analysis_id,
        source_id=f"source-{analysis_id}",
        source_name=f"来源 {analysis_id}",
        trust_tier=trust_tier,
        canonical_url=f"https://example.com/{analysis_id}",
        content_hash=str(analysis_id).zfill(64),
        title=f"Source title {analysis_id}",
        body="Source body",
        language="en",
        language_confidence=0.99,
        author=None,
        published_at=None,
        fetched_at=fetched_at or datetime(2026, 7, 20, 12, tzinfo=UTC),
        platforms=(Platform.EBAY,),
        regions=("global",),
        publisher_key=f"publisher-{analysis_id}.example" if trust_tier is TrustTier.MEDIA else None,
        attribution=f"媒体署名 {analysis_id}" if trust_tier is TrustTier.MEDIA else None,
        content_scope="metadata_only" if trust_tier is TrustTier.MEDIA else None,
    )
    result = AnalysisResult(
        headline_zh=f"eBay 政策更新 {analysis_id}",
        summary_zh=(
            f"eBay 发布政策更新 {analysis_id}，卖家需要核对适用站点、商品类别、生效日期与账户范围，"
            "重新评估对定价、库存和运营流程的影响，并在采取业务动作前核实官方原文，"
            "同时将结论同步给负责人持续跟进。"
        ),
        event_type=EventType.FEES,
        platforms=(Platform.EBAY,),
        regions=("global",),
        affected_seller_types=("all",),
        effective_at=None,
        risk_level=risk,
        impact=f"影响 {analysis_id}",
        rationale=(EvidenceClaim(claim="政策发生变化", quote="policy changed"),),
        action_items=(ActionItem(action=action, owner_type="运营"),),
        uncertainties=(),
        tags=("政策",),
    )
    return ScoredAnalysis(
        analysis_id=analysis_id,
        candidate=candidate,
        result=result,
        evidence_confidence=confidence,
        resolution=RiskResolution(risk_level=risk, rule_hits=(), needs_review=False),
        event_fingerprint=fingerprint or f"event-{analysis_id}",
    )


def test_report_window_is_previous_0900_inclusive_to_current_0900_exclusive() -> None:
    start, end = report_window(date(2026, 7, 21), ZoneInfo("Asia/Shanghai"))

    assert start == datetime(2026, 7, 20, 1, tzinfo=UTC)
    assert end == datetime(2026, 7, 21, 1, tzinfo=UTC)


def test_report_selects_at_most_15_unique_events_and_does_not_pad() -> None:
    composer = DailyReportComposer()
    three = tuple(_analysis(index) for index in range(1, 4))

    short_draft = composer.compose(report_date=date(2026, 7, 21), analyses=three)
    long_draft = composer.compose(
        report_date=date(2026, 7, 21),
        analyses=tuple(_analysis(index) for index in range(1, 17)),
    )

    assert len(short_draft.selected_analysis_ids) == 3
    assert len(long_draft.selected_analysis_ids) == 15
    assert short_draft.payload["sections"][0]["title"] == "AI 今日提炼"


def test_report_prefers_official_source_for_same_event() -> None:
    media = _analysis(
        1,
        fingerprint="same-event",
        confidence=99,
        risk=RiskLevel.HIGH,
        trust_tier=TrustTier.MEDIA,
    )
    official = _analysis(
        2,
        fingerprint="same-event",
        confidence=60,
        risk=RiskLevel.LOW,
        trust_tier=TrustTier.OFFICIAL,
    )

    draft = DailyReportComposer().compose(
        report_date=date(2026, 7, 21), analyses=(media, official)
    )

    assert draft.selected_analysis_ids == (official.analysis_id,)


def test_report_ranks_by_risk_then_confidence_then_recency() -> None:
    older = datetime(2026, 7, 20, 10, tzinfo=UTC)
    newer = older + timedelta(hours=1)
    analyses = (
        _analysis(1, risk=RiskLevel.LOW, confidence=99, fetched_at=newer),
        _analysis(2, risk=RiskLevel.HIGH, confidence=60, fetched_at=older),
        _analysis(3, risk=RiskLevel.MEDIUM, confidence=80, fetched_at=older),
        _analysis(4, risk=RiskLevel.MEDIUM, confidence=80, fetched_at=newer),
    )

    draft = DailyReportComposer().compose(
        report_date=date(2026, 7, 21), analyses=analyses
    )

    assert draft.selected_analysis_ids == (2, 4, 3, 1)


def test_report_uses_official_source_before_recency_for_tied_events() -> None:
    older = datetime(2026, 7, 20, 10, tzinfo=UTC)
    newer = older + timedelta(hours=1)
    official = _analysis(1, trust_tier=TrustTier.OFFICIAL, fetched_at=older)
    media = _analysis(2, trust_tier=TrustTier.MEDIA, fetched_at=newer)

    draft = DailyReportComposer().compose(
        report_date=date(2026, 7, 21), analyses=(media, official)
    )

    assert draft.selected_analysis_ids == (official.analysis_id, media.analysis_id)


def test_empty_day_builds_health_report_with_platform_source_coverage() -> None:
    coverage = (
        CoverageRow(Platform.EBAY, 1, 2, 0, 0, 0, 0),
        CoverageRow(Platform.TEMU, 0, 2, 0, 0, 0, 0),
    )

    draft = DailyReportComposer().compose(
        report_date=date(2026, 7, 21), analyses=(), coverage=coverage
    )
    encoded = json.dumps(draft.payload, ensure_ascii=False)

    assert draft.selected_analysis_ids == ()
    assert "ebay：无已验证更新" in encoded
    assert "temu：该平台尚无合规启用来源" in encoded


@pytest.mark.parametrize(
    ("enabled_sources", "verified_updates", "expected"),
    [
        (0, 0, "ebay：该平台尚无合规启用来源"),
        (1, 0, "ebay：无已验证更新"),
        (1, 2, "ebay：已验证 2 条"),
    ],
)
def test_b_and_health_reports_share_three_state_coverage_wording(
    enabled_sources: int,
    verified_updates: int,
    expected: str,
) -> None:
    coverage = (
        CoverageRow(
            Platform.EBAY,
            effective_source_count=enabled_sources,
            target_source_count=2,
            verified_update_count=verified_updates,
            full_text_update_count=verified_updates,
            feed_summary_count=0,
            metadata_only_count=0,
        ),
    )
    composer = DailyReportComposer()

    b_report = composer.compose(
        report_date=date(2026, 7, 21), analyses=(_analysis(1),), coverage=coverage
    )
    health_report = composer.compose(
        report_date=date(2026, 7, 21), analyses=(), coverage=coverage
    )

    assert b_report.payload["sections"][-1]["items"][0] == expected
    assert health_report.payload["sections"][-1]["items"][0] == expected


def test_conservative_daily_hides_unreviewed_model_actions() -> None:
    draft = DailyReportComposer().compose(
        report_date=date(2026, 7, 21),
        analyses=(_analysis(1, model_action="立即下架全部商品"),),
        profile=RiskProfile.CONSERVATIVE,
    )
    encoded = json.dumps(draft.payload, ensure_ascii=False)

    assert "策略：保守" in draft.payload["title"]
    assert draft.payload["risk_profile"] == "conservative"
    assert "人工复核原文和适用范围后再决定业务变更" in encoded
    assert "立即下架全部商品" not in encoded


def test_aggressive_pending_item_is_labeled_and_uses_only_reversible_action() -> None:
    draft = DailyReportComposer().compose(
        report_date=date(2026, 7, 21),
        analyses=(
            _analysis(1, confidence=70, model_action="立即删除全部列表"),
        ),
        profile=RiskProfile.AGGRESSIVE,
    )
    encoded = json.dumps(draft.payload, ensure_ascii=False)

    assert "策略：激进" in draft.payload["title"]
    assert "早期信号·待核实" in encoded
    assert "准备影响清单，不执行不可逆操作" in encoded
    assert "立即删除全部列表" not in encoded


def test_default_profile_keeps_verified_model_action() -> None:
    draft = DailyReportComposer().compose(
        report_date=date(2026, 7, 21),
        analyses=(_analysis(1, model_action="复核成本表"),),
    )

    assert "复核成本表" in json.dumps(draft.payload, ensure_ascii=False)


def test_report_items_include_risk_confidence_basis_action_attribution_and_original() -> None:
    draft = DailyReportComposer().compose(
        report_date=date(2026, 7, 21),
        analyses=(
            _analysis(1, confidence=90, trust_tier=TrustTier.OFFICIAL),
            _analysis(2, confidence=70, trust_tier=TrustTier.MEDIA),
        ),
    )

    items = {item["analysis_id"]: item for item in draft.payload["items"]}
    assert items[1]["verification_status"] == "verified"
    assert items[2]["verification_status"] == "early_signal"
    assert items[2]["risk_level"] == "medium"
    assert items[2]["evidence_confidence"] == 70
    assert items[2]["summary"]
    assert items[2]["rationale"][0]["quote"] == "policy changed"
    assert items[2]["actions"]
    assert items[2]["source_name"] == "媒体署名 2"
    assert items[2]["source_url"] == "https://example.com/2"
    assert items[2]["publisher_key"] == "publisher-2.example"


def test_media_report_item_uses_catalog_label_and_content_basis() -> None:
    media = _analysis(1)
    media = replace(
        media,
        candidate=replace(
            media.candidate,
            publisher_key="reuters.com",
            attribution="GDELT index; original publisher shown per item",
            content_scope="full_text",
        ),
    )

    draft = DailyReportComposer().compose(
        report_date=date(2026, 7, 21),
        analyses=(media,),
    )

    item = draft.payload["items"][0]
    assert item["source_name"] == "Reuters"
    assert item["media_category"] == "global_authority"
    assert item["content_basis"] == "full_text"


def test_profiles_change_actions_but_not_evidence_or_verification_status() -> None:
    analysis = _analysis(1, confidence=70, model_action="irreversible model action")
    payloads = {
        profile: DailyReportComposer().compose(
            report_date=date(2026, 7, 21),
            analyses=(analysis,),
            profile=profile,
        ).payload
        for profile in RiskProfile
    }

    items = {profile: payload["items"][0] for profile, payload in payloads.items()}
    assert {item["evidence_confidence"] for item in items.values()} == {70}
    assert {item["verification_status"] for item in items.values()} == {"early_signal"}
    assert len({json.dumps(item["actions"], ensure_ascii=False) for item in items.values()}) >= 2
    assert "irreversible model action" not in json.dumps(
        items[RiskProfile.DEFAULT]["actions"],
        ensure_ascii=False,
    )


class _RepositorySpy:
    def __init__(self, analyses: tuple[ScoredAnalysis, ...], coverage: tuple[CoverageRow, ...]):
        self.analyses = analyses
        self.coverage = coverage
        self.saved: tuple[str, object, datetime] | None = None
        self.previewed: int | None = None
        self.queued: tuple[int, datetime] | None = None
        self.variant: tuple[str, object, str, datetime] | None = None

    async def list_report_analyses(self, *, window_start, window_end):
        self.window = (window_start, window_end)
        return self.analyses

    async def list_coverage(self, *, window_start, window_end):
        assert (window_start, window_end) == self.window
        return self.coverage

    async def save_report(self, group_id, draft, *, now):
        self.saved = (group_id, draft, now)
        return 41

    async def mark_report_previewed(self, report_id):
        self.previewed = report_id

    async def get_report_id(self, group_id, report_date):
        del group_id, report_date
        return 41

    async def queue_report(self, report_id, *, now):
        self.queued = (report_id, now)
        return 73

    async def queue_report_variant(self, group_id, draft, *, variant, now):
        self.variant = (group_id, draft, variant, now)
        return 83


class _PreferenceSpy:
    def __init__(self, profile: RiskProfile):
        self.profile = profile
        self.request: tuple[str, RiskProfile] | None = None

    async def get(self, group_id: str, *, default: RiskProfile) -> RiskProfile:
        self.request = (group_id, default)
        return self.profile


async def test_preview_loads_current_group_profile_and_persists_that_payload() -> None:
    repository = _RepositorySpy((_analysis(1),), ())
    preferences = _PreferenceSpy(RiskProfile.CONSERVATIVE)
    now = datetime(2026, 7, 21, 1, 5, tzinfo=UTC)
    service = DailyReportService(
        repository,
        DailyReportComposer(),
        preferences,
        timezone=ZoneInfo("Asia/Shanghai"),
        clock=lambda: now,
    )

    draft = await service.preview("chat-one", date(2026, 7, 21))

    assert preferences.request == ("chat-one", RiskProfile.DEFAULT)
    assert draft.payload["risk_profile"] == "conservative"
    assert repository.saved == ("chat-one", draft, now)
    assert repository.previewed == 41


async def test_queue_previewed_rejects_a_report_whose_window_is_still_open() -> None:
    repository = _RepositorySpy((), ())
    now = datetime(2026, 7, 22, 5, tzinfo=UTC)
    service = DailyReportService(
        repository,
        DailyReportComposer(),
        _PreferenceSpy(RiskProfile.DEFAULT),
        timezone=ZoneInfo("Asia/Shanghai"),
        clock=lambda: now,
    )

    with pytest.raises(RuntimeError, match="daily report window is still open"):
        await service.queue_previewed("chat-one", date(2026, 7, 23))

    assert repository.queued is None


async def test_variant_build_does_not_save_an_official_report() -> None:
    repository = _RepositorySpy((_analysis(1),), ())
    now = datetime(2026, 7, 23, 1, 10, tzinfo=UTC)
    service = DailyReportService(
        repository,
        DailyReportComposer(),
        _PreferenceSpy(RiskProfile.DEFAULT),
        timezone=ZoneInfo("Asia/Shanghai"),
        clock=lambda: now,
    )

    outbox_id = await service.generate_variant_and_queue(
        "chat-one", date(2026, 7, 23), variant="test"
    )

    assert outbox_id == 83
    assert repository.saved is None
    assert repository.previewed is None
    assert repository.variant is not None
    assert repository.variant[0] == "chat-one"
    assert repository.variant[2:] == ("test", now)
