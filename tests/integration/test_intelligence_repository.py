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
    Platform,
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
from commerce_agent.persistence.models import AnalysisJob, DocumentAnalysis, DocumentVersion

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
