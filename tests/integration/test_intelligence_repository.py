from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    ContentScope,
    Platform,
    SourceAdapter,
    SourceDefinition,
    TrustTier,
)
from commerce_agent.intelligence.evidence import EvidenceScorer
from commerce_agent.intelligence.models import (
    ActionItem,
    AnalysisResult,
    EventType,
    EvidenceClaim,
    RiskLevel,
)
from commerce_agent.intelligence.reports import (
    DailyReportComposer,
    ReportAlreadySent,
)
from commerce_agent.intelligence.repository import (
    SqlAlchemyIntelligenceRepository,
    StaleLeaseError,
)
from commerce_agent.intelligence.risk import RiskPolicy, event_fingerprint
from commerce_agent.intelligence.service import AnalysisService
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.ingestion import (
    PersistableDocument,
    SqlAlchemyIngestionRepository,
)
from commerce_agent.persistence.models import (
    AnalysisDuplicate,
    AnalysisJob,
    DailyReport,
    DeliveryOutbox,
    DocumentAnalysis,
    DocumentVersion,
    SourceHealth,
)

NOW = datetime(2026, 7, 21, 1, tzinfo=UTC)


def _source(
    *,
    source_id: str = "amazon-news",
    compliance: ComplianceStatus = ComplianceStatus.ALLOWED,
) -> SourceDefinition:
    return SourceDefinition(
        source_id=source_id,
        name=f"Seller News {source_id}",
        entry_url="https://example.com/news",
        platforms=(Platform.AMAZON, Platform.EBAY),
        trust_tier=TrustTier.OFFICIAL,
        collector=CollectorKind.RSS,
        content_scope=ContentScope.FULL_TEXT,
        attribution=f"Seller News {source_id}",
        publisher_key=f"{source_id}.example.com",
        compliance=compliance,
        enabled=True,
        regions=("global",),
        language_hint="en",
        interval_minutes=120,
        terms_url="https://example.com/terms",
        robots_url="https://example.com/robots.txt",
        reviewed_at=date(2026, 7, 20),
        compliance_notes="Public feed approved for collection.",
        collector_config={"item_limit": 50},
    )


def _candidate(
    *,
    content_hash: str,
    canonical_url: str = "https://example.com/news/fee-update",
    source_id: str = "amazon-news",
    body: str = "Amazon changed a seller fee.",
) -> PersistableDocument:
    return PersistableDocument(
        source_id=source_id,
        canonical_url=canonical_url,
        title="Fee update",
        body=body,
        language="en",
        language_confidence=0.99,
        content_hash=content_hash,
        content_group_hash=f"group-{content_hash[0]}",
        fetched_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
        author="Amazon",
        published_at=datetime(2026, 7, 19, 8, tzinfo=UTC),
        publisher_key=f"{source_id}.example.com",
        attribution=f"Seller News {source_id}",
        content_scope="full_text",
    )


def _valid_result() -> AnalysisResult:
    return AnalysisResult(
        headline_zh="eBay 全球费用政策更新",
        summary_zh=(
            "eBay 发布新的费用政策说明，卖家需要核对适用站点、商品类别、生效日期与账户范围，"
            "重新测算商品毛利和活动预算，并在调整定价或运营策略前逐项复核官方原文规则，"
            "同时将结论同步给财务和负责人，确保关键费用变化得到及时处理和持续跟踪。"
        ),
        event_type=EventType.FEES,
        platforms=(Platform.EBAY,),
        regions=("global",),
        affected_seller_types=("all",),
        effective_at=datetime(2026, 7, 22, tzinfo=UTC),
        risk_level=RiskLevel.MEDIUM,
        impact="费用结构变化可能影响商品毛利",
        rationale=(EvidenceClaim(claim="费用发生变化", quote="fees will change"),),
        action_items=(ActionItem(action="复核成本表", owner_type="运营"),),
        uncertainties=(),
        tags=("费用",),
    )


async def _repositories(tmp_path, filename: str = "intelligence.db"):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / filename}")
    await database.create_schema()
    ingestion_repository = SqlAlchemyIngestionRepository(database.session)
    intelligence_repository = SqlAlchemyIntelligenceRepository(database.session)
    await ingestion_repository.sync_sources([_source()])
    await ingestion_repository.persist_version(_candidate(content_hash="a" * 64))
    return database, ingestion_repository, intelligence_repository


async def test_two_workers_cannot_claim_the_same_analysis_job(tmp_path) -> None:
    database, _, repository = await _repositories(tmp_path)
    competing_repository = SqlAlchemyIntelligenceRepository(database.session)
    try:
        first, second = await asyncio.gather(
            repository.claim_next(now=NOW),
            competing_repository.claim_next(now=NOW),
        )

        claimed = [item for item in (first, second) if item is not None]
        assert len(claimed) == 1
        assert claimed[0].lease_token is not None
    finally:
        await database.dispose()


async def test_claim_skips_jobs_from_denied_sources(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'denied-source.db'}")
    await database.create_schema()
    ingestion_repository = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    try:
        await ingestion_repository.sync_sources(
            [_source(compliance=ComplianceStatus.DENIED)]
        )
        outcome = await ingestion_repository.persist_version(
            _candidate(content_hash="d" * 64)
        )

        assert await repository.claim_next(now=NOW) is None

        async with database.session() as session:
            job = await session.scalar(
                select(AnalysisJob).where(
                    AnalysisJob.document_version_id == outcome.version_id
                )
            )

        assert job is not None
        assert job.status == "pending"
        assert job.attempt_count == 0
        assert job.lease_token is None
    finally:
        await database.dispose()


async def test_stale_worker_cannot_complete_reclaimed_lease(tmp_path) -> None:
    database, _, repository = await _repositories(tmp_path)
    try:
        old = await repository.claim_next(now=NOW, lease_seconds=1)
        assert old is not None
        fresh = await repository.claim_next(now=NOW + timedelta(seconds=2), lease_seconds=60)
        assert fresh is not None

        with pytest.raises(StaleLeaseError):
            await repository.complete_analysis(
                old,
                _valid_result(),
                90,
                "event-one",
                now=NOW + timedelta(seconds=3),
                model_name="test-model",
            )
    finally:
        await database.dispose()


async def test_expired_second_lease_is_failed_without_a_third_claim(tmp_path) -> None:
    database, _, repository = await _repositories(tmp_path)
    try:
        first = await repository.claim_next(now=NOW, lease_seconds=1)
        assert first is not None
        second = await repository.claim_next(
            now=NOW + timedelta(seconds=2), lease_seconds=1
        )
        assert second is not None

        third = await repository.claim_next(now=NOW + timedelta(seconds=4))

        async with database.session() as session:
            job = await session.get(AnalysisJob, first.job_id)

        assert third is None
        assert job is not None
        assert job.status == "failed"
        assert job.attempt_count == 2
        assert job.error_code == "lease_expired"
        assert job.lease_token is None
        assert job.lease_expires_at is None
    finally:
        await database.dispose()


async def test_stale_worker_cannot_fail_reclaimed_lease(tmp_path) -> None:
    database, _, repository = await _repositories(tmp_path)
    try:
        old = await repository.claim_next(now=NOW, lease_seconds=1)
        assert old is not None
        fresh = await repository.claim_next(
            now=NOW + timedelta(seconds=2), lease_seconds=60
        )
        assert fresh is not None

        with pytest.raises(StaleLeaseError):
            await repository.fail_analysis(
                old, "stale_worker_failure", now=NOW + timedelta(seconds=3)
            )

        async with database.session() as session:
            job = await session.get(AnalysisJob, old.job_id)

        assert job is not None
        assert job.status == "running"
        assert job.attempt_count == 2
        assert job.lease_token == fresh.lease_token
        assert job.error_code is None
    finally:
        await database.dispose()


async def test_complete_analysis_persists_payload_after_guarded_transition(tmp_path) -> None:
    database, _, repository = await _repositories(tmp_path)
    try:
        claim = await repository.claim_next(now=NOW)
        assert claim is not None

        analysis_id = await repository.complete_analysis(
            claim,
            _valid_result(),
            87,
            "event-completed",
            now=NOW + timedelta(seconds=4),
            model_name="test-model",
            schema_version="2",
            prompt_version="3",
        )

        async with database.session() as session:
            job = await session.get(AnalysisJob, claim.job_id)
            analysis = await session.get(DocumentAnalysis, analysis_id)

        assert job is not None
        assert job.status == "completed"
        assert job.lease_token is None
        assert analysis is not None
        assert analysis.document_version_id == claim.document_version_id
        assert analysis.evidence_confidence == 87
        assert analysis.event_fingerprint == "event-completed"
        assert analysis.structured_payload["event_type"] == "fees"
        assert (analysis.schema_version, analysis.prompt_version, analysis.model_name) == (
            "2",
            "3",
            "test-model",
        )
    finally:
        await database.dispose()


async def test_complete_analysis_indexes_resolved_risk_but_preserves_model_payload(
    tmp_path,
) -> None:
    database, _, repository = await _repositories(tmp_path)
    try:
        claim = await repository.claim_next(now=NOW)
        assert claim is not None
        result = _valid_result().model_copy(
            update={
                "event_type": EventType.ACCOUNT_ENFORCEMENT,
                "risk_level": RiskLevel.LOW,
            }
        )

        await repository.complete_analysis(
            claim,
            result,
            90,
            event_fingerprint(result, subject=result.headline_zh),
            risk_level=RiskLevel.HIGH,
            now=NOW,
            model_name="test-model",
        )

        rows = await repository.list_analyses()
        assert len(rows) == 1
        assert rows[0].risk_level == RiskLevel.HIGH.value
        assert rows[0].structured_payload["risk_level"] == RiskLevel.LOW.value
        assert (
            await repository.count_corroborating_sources(
                event_fingerprint(result, subject=result.headline_zh),
                claim,
            )
            == 1
        )
    finally:
        await database.dispose()


class _StaticAnalyzer:
    def __init__(self, result: AnalysisResult) -> None:
        self._result = result

    async def analyze(self, candidate) -> AnalysisResult:
        del candidate
        return self._result


async def test_drain_scores_two_batch_sources_as_corroborating(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'two-sources.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    body = "fees will change on 2026-07-22. " + "Policy detail. " * 35
    try:
        await ingestion.sync_sources(
            [_source(source_id="source-one"), _source(source_id="source-two")]
        )
        await ingestion.persist_version(
            _candidate(
                source_id="source-one",
                content_hash="1" * 64,
                canonical_url="https://example.com/source-one",
                body=body,
            )
        )
        await ingestion.persist_version(
            _candidate(
                source_id="source-two",
                content_hash="2" * 64,
                canonical_url="https://example.com/source-two",
                body=body,
            )
        )

        batch = await AnalysisService(
            repository,
            _StaticAnalyzer(_valid_result()),
            EvidenceScorer(),
            RiskPolicy(),
            concurrency=2,
            model_name="test-model",
            clock=lambda: NOW,
        ).drain(limit=2)

        assert batch.succeeded == 2
        assert [item.evidence_confidence for item in batch.completed] == [100, 100]
    finally:
        await database.dispose()


async def test_same_source_batch_rows_do_not_add_corroboration_points(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'same-source.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    body = "fees will change on 2026-07-22. " + "Policy detail. " * 35
    try:
        await ingestion.sync_sources([_source(source_id="only-source")])
        for index in (1, 2):
            await ingestion.persist_version(
                _candidate(
                    source_id="only-source",
                    content_hash=str(index) * 64,
                    canonical_url=f"https://example.com/only-source/{index}",
                    body=body,
                )
            )

        batch = await AnalysisService(
            repository,
            _StaticAnalyzer(_valid_result()),
            EvidenceScorer(),
            RiskPolicy(),
            concurrency=2,
            model_name="test-model",
            clock=lambda: NOW,
        ).drain(limit=2)

        assert batch.succeeded == 2
        assert [item.evidence_confidence for item in batch.completed] == [90, 90]
    finally:
        await database.dispose()


async def test_claim_skips_metadata_only_documents(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'metadata-only.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    media_source = replace(
        _source(source_id="media-gdelt-cross-border"),
        trust_tier=TrustTier.MEDIA,
        adapter=SourceAdapter.GDELT,
        content_scope=ContentScope.METADATA_ONLY,
        attribution="GDELT index; original publisher shown per item",
        publisher_key=None,
    )
    try:
        await ingestion.sync_sources([media_source])
        outcome = await ingestion.persist_version(
            replace(
                _candidate(
                    source_id=media_source.source_id,
                    content_hash="m" * 64,
                    canonical_url="https://www.reuters.com/example",
                ),
                publisher_key="reuters.com",
                attribution=media_source.attribution,
                content_scope="metadata_only",
            )
        )

        assert await repository.claim_next(now=NOW) is None
        async with database.session() as session:
            job = await session.get(AnalysisJob, outcome.version_id)
        assert job is not None
        assert job.status == "pending"
    finally:
        await database.dispose()


async def test_same_body_from_email_and_feishu_is_analyzed_once(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'cross-channel.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    email_source = replace(
        _source(source_id="amazon-email"),
        platforms=(Platform.AMAZON,),
    )
    feishu_source = replace(
        _source(source_id="ebay-feishu"),
        platforms=(Platform.EBAY,),
    )
    try:
        await ingestion.sync_sources((email_source, feishu_source))
        first_outcome = await ingestion.persist_version(
            replace(
                _candidate(
                    source_id=email_source.source_id,
                    content_hash="a" * 64,
                    canonical_url="https://example.com/email",
                ),
                content_group_hash="shared-body",
            )
        )
        second_outcome = await ingestion.persist_version(
            replace(
                _candidate(
                    source_id=feishu_source.source_id,
                    content_hash="b" * 64,
                    canonical_url="https://example.com/feishu",
                ),
                content_group_hash="shared-body",
            )
        )

        first = await repository.claim_next(now=NOW)
        assert first is not None
        assert await repository.claim_next(now=NOW) is None
        analysis_id = await repository.complete_analysis(
            first,
            _valid_result(),
            90,
            "event-1",
            risk_level=RiskLevel.HIGH,
            now=NOW,
            model_name="test-model",
        )

        assert await repository.claim_next(now=NOW) is None
        async with database.session() as session:
            second_job = await session.scalar(
                select(AnalysisJob).where(
                    AnalysisJob.document_version_id == second_outcome.version_id
                )
            )
            duplicate = await session.get(AnalysisDuplicate, second_outcome.version_id)
        assert second_job is not None
        assert second_job.status == "duplicate"
        assert duplicate is not None
        assert duplicate.canonical_analysis_id == analysis_id

        analyses = await repository.list_report_analyses(
            window_start=datetime(2026, 7, 20, tzinfo=UTC),
            window_end=datetime(2026, 7, 22, tzinfo=UTC),
        )
        assert len(analyses) == 1
        assert analyses[0].candidate.document_version_id == first_outcome.version_id
        assert analyses[0].candidate.platforms == (
            Platform.AMAZON,
            Platform.EBAY,
        )
        assert len(analyses[0].candidate.source_references) == 2
    finally:
        await database.dispose()


async def test_claim_skips_feed_summary_documents(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'feed-summary.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    summary_source = replace(
        _source(source_id="amazon-summary"),
        content_scope=ContentScope.FEED_SUMMARY,
        attribution="Amazon",
        publisher_key="amazon.com",
    )
    try:
        await ingestion.sync_sources([summary_source])
        await ingestion.persist_version(
            replace(
                _candidate(
                    source_id=summary_source.source_id,
                    content_hash="s" * 64,
                ),
                publisher_key="amazon.com",
                attribution="Amazon",
                content_scope="feed_summary",
            )
        )

        assert await repository.claim_next(now=NOW) is None
    finally:
        await database.dispose()


@pytest.mark.parametrize(
    ("publishers", "expected_count"),
    [(("reuters.com", "reuters.com"), 1), (("reuters.com", "apnews.com"), 2)],
)
async def test_media_corroboration_counts_publishers_not_collection_channels(
    tmp_path,
    publishers: tuple[str, str],
    expected_count: int,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'publisher-count.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    sources = [
        replace(
            _source(source_id=f"media-{index}"),
            trust_tier=TrustTier.MEDIA,
            adapter=SourceAdapter.GENERIC,
            content_scope=ContentScope.FULL_TEXT,
            attribution=f"Publisher {index}",
            publisher_key=publisher,
        )
        for index, publisher in enumerate(publishers, start=1)
    ]
    try:
        await ingestion.sync_sources(sources)
        for index, publisher in enumerate(publishers, start=1):
            await ingestion.persist_version(
                replace(
                    _candidate(
                        source_id=f"media-{index}",
                        content_hash=str(index) * 64,
                        canonical_url=f"https://{publisher}/story/{index}",
                    ),
                    publisher_key=publisher,
                    attribution=f"Publisher {index}",
                    content_scope="full_text",
                )
            )

        first = await repository.claim_next(now=NOW)
        second = await repository.claim_next(now=NOW)

        assert first is not None
        assert second is not None
        assert {first.publisher_key, second.publisher_key} == set(publishers)
        count = await repository.count_corroborating_sources(
            "same-event",
            first,
            batch_claims=(second,),
        )
        assert count == expected_count
    finally:
        await database.dispose()


async def test_superseded_analysis_is_excluded_from_corroborating_sources(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'superseded.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    try:
        await ingestion.sync_sources(
            [_source(source_id="old-source"), _source(source_id="current-source")]
        )
        await ingestion.persist_version(
            _candidate(
                source_id="old-source",
                content_hash="a" * 64,
                canonical_url="https://example.com/old-source",
            )
        )
        old_claim = await repository.claim_next(now=NOW)
        assert old_claim is not None
        result = _valid_result()
        fingerprint = event_fingerprint(result, subject=result.headline_zh)
        await repository.complete_analysis(
            old_claim,
            result,
            90,
            fingerprint,
            now=NOW,
            model_name="test-model",
        )

        await ingestion.persist_version(
            _candidate(
                source_id="old-source",
                content_hash="b" * 64,
                canonical_url="https://example.com/old-source",
            )
        )
        await ingestion.persist_version(
            _candidate(
                source_id="current-source",
                content_hash="c" * 64,
                canonical_url="https://example.com/current-source",
            )
        )
        pending_claims = [
            await repository.claim_next(now=NOW),
            await repository.claim_next(now=NOW),
        ]
        current_claim = next(
            claim
            for claim in pending_claims
            if claim is not None and claim.source_id == "current-source"
        )

        count = await repository.count_corroborating_sources(
            fingerprint,
            current_claim,
        )

        assert count == 1
    finally:
        await database.dispose()


async def test_analysis_insert_failure_rolls_back_completed_job_transition(tmp_path) -> None:
    database, _, repository = await _repositories(tmp_path)
    try:
        claim = await repository.claim_next(now=NOW)
        assert claim is not None
        result = _valid_result()
        async with database.session.begin() as session:
            session.add(
                DocumentAnalysis(
                    document_version_id=claim.document_version_id,
                    schema_version="existing",
                    prompt_version="existing",
                    model_name="existing-model",
                    headline_zh=result.headline_zh,
                    summary_zh=result.summary_zh,
                    event_type=result.event_type.value,
                    risk_level=result.risk_level.value,
                    evidence_confidence=50,
                    event_fingerprint="existing-event",
                    structured_payload=result.model_dump(mode="json"),
                    analyzed_at=NOW,
                )
            )

        with pytest.raises(IntegrityError):
            await repository.complete_analysis(
                claim,
                result,
                90,
                "duplicate-analysis",
                now=NOW + timedelta(seconds=1),
                model_name="test-model",
            )

        async with database.session() as session:
            job = await session.get(AnalysisJob, claim.job_id)

        assert job is not None
        assert job.status == "running"
        assert job.lease_token == claim.lease_token
        assert job.lease_expires_at == NOW + timedelta(minutes=5)
    finally:
        await database.dispose()


async def test_fail_analysis_retries_once_then_marks_job_failed(tmp_path) -> None:
    database, _, repository = await _repositories(tmp_path)
    try:
        first = await repository.claim_next(now=NOW)
        assert first is not None
        await repository.fail_analysis(first, "provider_timeout", now=NOW)

        assert await repository.claim_next(now=NOW + timedelta(minutes=4)) is None
        second = await repository.claim_next(now=NOW + timedelta(minutes=5))
        assert second is not None
        await repository.fail_analysis(
            second, "invalid_response", now=NOW + timedelta(minutes=5)
        )

        async with database.session() as session:
            job = await session.get(AnalysisJob, first.job_id)

        assert job is not None
        assert job.status == "failed"
        assert job.attempt_count == 2
        assert job.next_attempt_at is None
        assert job.error_code == "invalid_response"
        assert job.lease_token is None
    finally:
        await database.dispose()


async def test_lease_transitions_reject_claim_without_token(tmp_path) -> None:
    database, _, repository = await _repositories(tmp_path)
    try:
        claim = await repository.claim_next(now=NOW)
        assert claim is not None
        tokenless = replace(claim, lease_token=None)

        with pytest.raises(StaleLeaseError):
            await repository.fail_analysis(tokenless, "invalid_claim", now=NOW)
        with pytest.raises(StaleLeaseError):
            await repository.complete_analysis(
                tokenless,
                _valid_result(),
                80,
                "event-tokenless",
                now=NOW,
                model_name="test-model",
            )
    finally:
        await database.dispose()


async def test_report_query_uses_current_allowed_versions_and_exact_window(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'report-query.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    start = datetime(2026, 7, 20, 1, tzinfo=UTC)
    end = datetime(2026, 7, 21, 1, tzinfo=UTC)
    try:
        await ingestion.sync_sources(
            [
                _source(source_id="allowed"),
                _source(source_id="denied", compliance=ComplianceStatus.DENIED),
            ]
        )

        async def add_analysis(
            source_id: str,
            url: str,
            fetched_at: datetime,
            confidence: int,
            fingerprint: str,
        ) -> int:
            outcome = await ingestion.persist_version(
                replace(
                    _candidate(
                        source_id=source_id,
                        content_hash=fingerprint[0] * 64,
                        canonical_url=url,
                    ),
                    fetched_at=fetched_at,
                )
            )
            async with database.session.begin() as session:
                analysis = DocumentAnalysis(
                    document_version_id=outcome.version_id,
                    schema_version="1",
                    prompt_version="1",
                    model_name="test-model",
                    headline_zh=_valid_result().headline_zh,
                    summary_zh=_valid_result().summary_zh,
                    event_type=_valid_result().event_type.value,
                    risk_level=_valid_result().risk_level.value,
                    evidence_confidence=confidence,
                    event_fingerprint=fingerprint,
                    structured_payload=_valid_result().model_dump(mode="json"),
                    analyzed_at=NOW,
                )
                session.add(analysis)
                await session.flush()
                job = await session.scalar(
                    select(AnalysisJob).where(
                        AnalysisJob.document_version_id == outcome.version_id
                    )
                )
                assert job is not None
                job.status = "completed"
                return analysis.id

        expected_id = await add_analysis("allowed", "https://example.com/start", start, 60, "a")
        await add_analysis("allowed", "https://example.com/end", end, 90, "b")
        await add_analysis(
            "denied", "https://example.com/denied", start + timedelta(hours=1), 90, "c"
        )
        await add_analysis(
            "allowed", "https://example.com/low", start + timedelta(hours=2), 59, "d"
        )
        old_id = await add_analysis(
            "allowed", "https://example.com/current", start + timedelta(hours=3), 90, "e"
        )
        await ingestion.persist_version(
            replace(
                _candidate(
                    source_id="allowed",
                    content_hash="f" * 64,
                    canonical_url="https://example.com/current",
                ),
                fetched_at=end + timedelta(hours=1),
            )
        )

        rows = await repository.list_report_analyses(window_start=start, window_end=end)

        assert [row.analysis_id for row in rows] == [expected_id]
        assert old_id not in {row.analysis_id for row in rows}
        assert rows[0].candidate.fetched_at == start
    finally:
        await database.dispose()


async def test_coverage_lists_effective_publishers_material_scopes_and_anomalies(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'coverage.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    start = datetime(2026, 7, 20, 1, tzinfo=UTC)
    end = datetime(2026, 7, 21, 1, tzinfo=UTC)
    try:
        full_source = _source(source_id="enabled-full")
        summary_source = replace(
            _source(source_id="enabled-summary"),
            content_scope=ContentScope.FEED_SUMMARY,
            publisher_key="summary.example.com",
        )
        metadata_source = replace(
            _source(source_id="enabled-metadata"),
            content_scope=ContentScope.METADATA_ONLY,
            publisher_key="metadata.example.com",
        )
        suspended_source = replace(
            _source(source_id="suspended-full"),
            publisher_key="suspended.example.com",
        )
        await ingestion.sync_sources(
            [full_source, summary_source, metadata_source, suspended_source]
        )
        outcome = await ingestion.persist_version(
            replace(
                _candidate(source_id=full_source.source_id, content_hash="7" * 64),
                fetched_at=start,
            )
        )
        await ingestion.persist_version(
            replace(
                _candidate(source_id=summary_source.source_id, content_hash="6" * 64),
                fetched_at=start,
                publisher_key=summary_source.publisher_key,
                attribution=summary_source.attribution,
                content_scope="feed_summary",
            )
        )
        await ingestion.persist_version(
            replace(
                _candidate(source_id=metadata_source.source_id, content_hash="5" * 64),
                fetched_at=start,
                publisher_key=metadata_source.publisher_key,
                attribution=metadata_source.attribution,
                content_scope="metadata_only",
            )
        )
        async with database.session.begin() as session:
            session.add(
                SourceHealth(
                    source_id=suspended_source.source_id,
                    consecutive_failures=3,
                    last_error_code="timeout",
                    health_status="suspended",
                )
            )
            job = await session.scalar(
                select(AnalysisJob).where(
                    AnalysisJob.document_version_id == outcome.version_id
                )
            )
            assert job is not None
            job.status = "completed"
            session.add(
                DocumentAnalysis(
                    document_version_id=outcome.version_id,
                    schema_version="1",
                    prompt_version="1",
                    model_name="test-model",
                    headline_zh=_valid_result().headline_zh,
                    summary_zh=_valid_result().summary_zh,
                    event_type=_valid_result().event_type.value,
                    risk_level=_valid_result().risk_level.value,
                    evidence_confidence=75,
                    event_fingerprint="coverage-event",
                    structured_payload=_valid_result().model_dump(mode="json"),
                    analyzed_at=NOW,
                )
            )

        rows = await repository.list_coverage(window_start=start, window_end=end)
        by_platform = {row.platform: row for row in rows}

        assert set(by_platform) == set(Platform)
        assert by_platform[Platform.EBAY].effective_source_count == 1
        assert by_platform[Platform.EBAY].target_source_count == 2
        assert by_platform[Platform.EBAY].verified_update_count == 1
        assert by_platform[Platform.EBAY].full_text_update_count == 1
        assert by_platform[Platform.EBAY].feed_summary_count == 1
        assert by_platform[Platform.EBAY].metadata_only_count == 1
        assert by_platform[Platform.EBAY].source_anomalies == (
            "suspended-full:suspended:timeout",
        )
        assert by_platform[Platform.TEMU].effective_source_count == 0
        assert by_platform[Platform.TEMU].verified_update_count == 0
    finally:
        await database.dispose()


async def test_coverage_ignores_legacy_non_full_text_analysis(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'legacy-summary.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    start = datetime(2026, 7, 20, 1, tzinfo=UTC)
    end = datetime(2026, 7, 21, 1, tzinfo=UTC)
    source = replace(
        _source(source_id="legacy-summary"),
        content_scope=ContentScope.FEED_SUMMARY,
    )
    try:
        await ingestion.sync_sources([source])
        outcome = await ingestion.persist_version(
            replace(
                _candidate(source_id=source.source_id, content_hash="4" * 64),
                fetched_at=start,
                content_scope="feed_summary",
            )
        )
        async with database.session.begin() as session:
            job = await session.get(AnalysisJob, outcome.version_id)
            assert job is not None
            job.status = "completed"
            session.add(
                DocumentAnalysis(
                    document_version_id=outcome.version_id,
                    schema_version="1",
                    prompt_version="1",
                    model_name="legacy-test-model",
                    headline_zh=_valid_result().headline_zh,
                    summary_zh=_valid_result().summary_zh,
                    event_type=_valid_result().event_type.value,
                    risk_level=_valid_result().risk_level.value,
                    evidence_confidence=90,
                    event_fingerprint="legacy-summary-event",
                    structured_payload=_valid_result().model_dump(mode="json"),
                    analyzed_at=NOW,
                )
            )

        row = next(
            item
            for item in await repository.list_coverage(
                window_start=start,
                window_end=end,
            )
            if item.platform is Platform.EBAY
        )

        assert row.verified_update_count == 0
        assert row.feed_summary_count == 1
    finally:
        await database.dispose()


async def test_reports_and_coverage_require_completed_analysis_jobs(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'completed-reports.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    start = datetime(2026, 7, 20, 1, tzinfo=UTC)
    end = datetime(2026, 7, 21, 1, tzinfo=UTC)
    try:
        await ingestion.sync_sources([_source(source_id="completed-only")])
        outcome = await ingestion.persist_version(
            replace(
                _candidate(source_id="completed-only", content_hash="8" * 64),
                fetched_at=start,
            )
        )
        async with database.session.begin() as session:
            analysis = DocumentAnalysis(
                document_version_id=outcome.version_id,
                schema_version="1",
                prompt_version="1",
                model_name="test-model",
                headline_zh=_valid_result().headline_zh,
                summary_zh=_valid_result().summary_zh,
                event_type=_valid_result().event_type.value,
                risk_level=_valid_result().risk_level.value,
                evidence_confidence=75,
                event_fingerprint="pending-event",
                structured_payload=_valid_result().model_dump(mode="json"),
                analyzed_at=NOW,
            )
            session.add(analysis)
            await session.flush()
            analysis_id = analysis.id

        pending_reports = await repository.list_report_analyses(
            window_start=start, window_end=end
        )
        pending_coverage = await repository.list_coverage(
            window_start=start, window_end=end
        )

        assert pending_reports == ()
        assert next(
            row for row in pending_coverage if row.platform is Platform.EBAY
        ).verified_update_count == 0

        async with database.session.begin() as session:
            job = await session.scalar(
                select(AnalysisJob).where(
                    AnalysisJob.document_version_id == outcome.version_id
                )
            )
            assert job is not None
            job.status = "completed"

        completed_reports = await repository.list_report_analyses(
            window_start=start, window_end=end
        )
        completed_coverage = await repository.list_coverage(
            window_start=start, window_end=end
        )

        assert [row.analysis_id for row in completed_reports] == [analysis_id]
        assert next(
            row for row in completed_coverage if row.platform is Platform.EBAY
        ).verified_update_count == 1
    finally:
        await database.dispose()


async def test_report_preview_and_queue_transitions_are_idempotent(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'report-save.db'}")
    await database.create_schema()
    repository = SqlAlchemyIntelligenceRepository(database.session)
    now = datetime(2026, 7, 21, 1, 5, tzinfo=UTC)
    draft = DailyReportComposer().compose(report_date=date(2026, 7, 21), analyses=())
    try:
        report_id = await repository.save_report("chat-one", draft, now=now)
        assert await repository.save_report("chat-one", draft, now=now) == report_id

        await repository.mark_report_previewed(report_id)
        await repository.mark_report_previewed(report_id)
        first_outbox_id = await repository.queue_report(report_id, now=now)
        second_outbox_id = await repository.queue_report(report_id, now=now)

        async with database.session() as session:
            report = await session.get(DailyReport, report_id)
            outbox_rows = (
                await session.scalars(select(DeliveryOutbox).order_by(DeliveryOutbox.id))
            ).all()

        assert first_outbox_id == second_outbox_id
        assert report is not None and report.status == "queued"
        assert len(outbox_rows) == 1
        assert outbox_rows[0].idempotency_key == "daily:chat-one:2026-07-21"
        assert outbox_rows[0].payload == draft.payload

        changed = replace(draft, payload={"changed": True})
        with pytest.raises(RuntimeError, match="already queued"):
            await repository.save_report("chat-one", changed, now=now)
    finally:
        await database.dispose()


async def test_sent_report_cannot_be_overwritten(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'sent-report.db'}")
    await database.create_schema()
    repository = SqlAlchemyIntelligenceRepository(database.session)
    now = datetime(2026, 7, 21, 1, 5, tzinfo=UTC)
    draft = DailyReportComposer().compose(report_date=date(2026, 7, 21), analyses=())
    try:
        report_id = await repository.save_report("chat-one", draft, now=now)
        async with database.session.begin() as session:
            report = await session.get(DailyReport, report_id)
            assert report is not None
            report.status = "sent"
            report.sent_at = now

        changed = replace(draft, payload={"changed": True})
        with pytest.raises(ReportAlreadySent):
            await repository.save_report("chat-one", changed, now=now)
        with pytest.raises(ReportAlreadySent):
            await repository.queue_report(report_id, now=now)

        async with database.session() as session:
            stored = await session.get(DailyReport, report_id)

        assert stored is not None
        assert stored.report_payload == draft.payload
    finally:
        await database.dispose()


async def test_correction_report_is_idempotent_and_marks_sent_without_official_row(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'report-correction.db'}")
    await database.create_schema()
    repository = SqlAlchemyIntelligenceRepository(database.session)
    draft = DailyReportComposer().compose(report_date=date(2026, 7, 23), analyses=())
    now = datetime(2026, 7, 23, 1, 10, tzinfo=UTC)
    try:
        first = await repository.queue_report_variant(
            "chat-one", draft, variant="correction", now=now
        )
        second = await repository.queue_report_variant(
            "chat-one", draft, variant="correction", now=now
        )
        claim = await repository.claim_delivery_by_id(first, now=now)
        assert claim is not None

        await repository.mark_delivery_sent(claim, message_id="om_correction", now=now)

        async with database.session() as session:
            outbox = await session.get(DeliveryOutbox, first)
            reports = (await session.scalars(select(DailyReport))).all()

        assert first == second
        assert outbox is not None
        assert outbox.idempotency_key.startswith(
            "daily-correction:chat-one:2026-07-23:"
        )
        assert outbox.status == "sent"
        assert outbox.feishu_message_id == "om_correction"
        assert outbox.payload["title"].startswith("补发 · ")
        assert reports == []
    finally:
        await database.dispose()


async def test_report_variant_rejects_unknown_namespace(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'report-variant.db'}")
    await database.create_schema()
    repository = SqlAlchemyIntelligenceRepository(database.session)
    draft = DailyReportComposer().compose(report_date=date(2026, 7, 23), analyses=())
    try:
        with pytest.raises(ValueError, match="unsupported report delivery variant"):
            await repository.queue_report_variant(
                "chat-one", draft, variant="unknown", now=NOW
            )
    finally:
        await database.dispose()


async def test_backfill_jobs_adds_missing_versions_up_to_limit(tmp_path) -> None:
    database, ingestion_repository, repository = await _repositories(tmp_path)
    try:
        second = await ingestion_repository.persist_version(
            _candidate(content_hash="b" * 64, canonical_url="https://example.com/second")
        )
        third = await ingestion_repository.persist_version(
            _candidate(content_hash="c" * 64, canonical_url="https://example.com/third")
        )
        async with database.session.begin() as session:
            await session.execute(delete(AnalysisJob))

        assert await repository.backfill_jobs(limit=2) == 2
        assert await repository.backfill_jobs(limit=2) == 1
        assert await repository.backfill_jobs(limit=2) == 0

        async with database.session() as session:
            jobs = (await session.scalars(select(AnalysisJob).order_by(AnalysisJob.id))).all()
            version_ids = set(await session.scalars(select(DocumentVersion.id)))

        assert {job.document_version_id for job in jobs} == version_ids
        assert {second.version_id, third.version_id}.issubset(version_ids)
    finally:
        await database.dispose()
