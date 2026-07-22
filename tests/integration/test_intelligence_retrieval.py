from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import update

from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    Platform,
    SourceDefinition,
    TrustTier,
)
from commerce_agent.intelligence.models import RiskLevel
from commerce_agent.intelligence.repository import SqlAlchemyIntelligenceRepository
from commerce_agent.intelligence.retrieval import CorpusQuery, CorpusRetriever
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.ingestion import (
    PersistableDocument,
    SqlAlchemyIngestionRepository,
)
from commerce_agent.persistence.models import AnalysisJob, DocumentAnalysis

NOW = datetime(2026, 7, 21, 1, tzinfo=UTC)


def _source(
    source_id: str,
    *,
    platform: Platform = Platform.EBAY,
    region: str = "eu",
    compliance: ComplianceStatus = ComplianceStatus.ALLOWED,
    enabled: bool = True,
) -> SourceDefinition:
    return SourceDefinition(
        source_id=source_id,
        name=f"Source {source_id}",
        entry_url=f"https://example.com/{source_id}",
        platforms=(platform,),
        trust_tier=TrustTier.OFFICIAL,
        collector=CollectorKind.RSS,
        compliance=compliance,
        enabled=enabled,
        regions=(region,),
        language_hint="en",
        interval_minutes=120,
        terms_url="https://example.com/terms",
        robots_url="https://example.com/robots.txt",
        reviewed_at=date(2026, 7, 20),
        compliance_notes="Public test source.",
        collector_config={"item_limit": 50},
    )


def _document(
    source_id: str,
    marker: str,
    *,
    title: str,
    fetched_at: datetime = NOW - timedelta(days=1),
    published_at: datetime | None = None,
    url_suffix: str | None = None,
) -> PersistableDocument:
    return PersistableDocument(
        source_id=source_id,
        canonical_url=f"https://example.com/{source_id}/{url_suffix or marker}",
        title=title,
        body=f"Untrusted source body {marker}",
        language="en",
        language_confidence=0.99,
        content_hash=marker * 64,
        content_group_hash=f"group-{source_id}-{marker}",
        fetched_at=fetched_at,
        author=None,
        published_at=published_at if published_at is not None else fetched_at,
    )


async def _add_analysis(
    database: Database,
    version_id: int,
    *,
    title: str,
    risk: RiskLevel = RiskLevel.MEDIUM,
    confidence: int = 80,
    job_status: str = "completed",
) -> int:
    async with database.session.begin() as session:
        updated = await session.execute(
            update(AnalysisJob)
            .where(AnalysisJob.document_version_id == version_id)
            .values(status=job_status, updated_at=NOW)
        )
        assert updated.rowcount == 1
        analysis = DocumentAnalysis(
            document_version_id=version_id,
            schema_version="1",
            prompt_version="1",
            model_name="test-model",
            headline_zh=title,
            summary_zh=f"{title} policy evidence summary",
            event_type="policy",
            risk_level=risk.value,
            evidence_confidence=confidence,
            event_fingerprint=f"event-{version_id}",
            structured_payload={
                "rationale": [
                    {"claim": "The policy changed", "quote": f"evidence {title}"},
                    {"claim": "Malformed quote is ignored", "quote": 42},
                ]
            },
            analyzed_at=NOW,
        )
        session.add(analysis)
        await session.flush()
        return analysis.id


async def test_real_sqlite_returns_only_current_analyzed_currently_allowed_sources(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'retrieval-compliance.db'}")
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    retriever = CorpusRetriever(repository)
    sources = [
        _source("allowed-disabled", enabled=False),
        _source("later-denied"),
        _source("superseded"),
        _source("unanalyzed"),
    ]
    try:
        await database.create_schema()
        await ingestion.sync_sources(sources)

        allowed = await ingestion.persist_version(
            _document("allowed-disabled", "a", title="Policy allowed disabled")
        )
        await _add_analysis(database, allowed.version_id, title="allowed-disabled")

        denied = await ingestion.persist_version(
            _document("later-denied", "b", title="Policy later denied")
        )
        await _add_analysis(database, denied.version_id, title="later-denied")

        old = await ingestion.persist_version(
            _document("superseded", "c", title="Policy superseded", url_suffix="same")
        )
        await _add_analysis(database, old.version_id, title="superseded-old")
        current = await ingestion.persist_version(
            _document("superseded", "d", title="Policy current", url_suffix="same")
        )
        await _add_analysis(database, current.version_id, title="superseded-current")

        await ingestion.persist_version(_document("unanalyzed", "e", title="Policy unanalyzed"))
        await ingestion.sync_sources(
            [
                sources[0],
                replace(sources[1], compliance=ComplianceStatus.DENIED),
                sources[2],
                sources[3],
            ]
        )

        results = await retriever.search(CorpusQuery(text="policy", now=NOW))

        assert {result.source_id for result in results} == {
            "allowed-disabled",
            "superseded",
        }
        assert old.version_id not in {result.document_version_id for result in results}
        assert current.version_id in {result.document_version_id for result in results}
        disabled_result = next(
            result for result in results if result.source_id == "allowed-disabled"
        )
        assert disabled_result.evidence_quotes == ("evidence allowed-disabled",)
    finally:
        await database.dispose()


async def test_real_sqlite_requires_completed_job_even_when_analysis_row_exists(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'retrieval-job-state.db'}")
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    retriever = CorpusRetriever(repository)
    statuses = ("completed", "pending", "failed")
    try:
        await database.create_schema()
        await ingestion.sync_sources([_source(f"job-{status}") for status in statuses])
        for index, status in enumerate(statuses):
            outcome = await ingestion.persist_version(
                _document(
                    f"job-{status}",
                    chr(ord("k") + index),
                    title=f"Policy job {status}",
                )
            )
            await _add_analysis(
                database,
                outcome.version_id,
                title=f"job-{status}",
                job_status=status,
            )

        results = await retriever.search(CorpusQuery(text="policy", now=NOW))

        assert [result.source_id for result in results] == ["job-completed"]
    finally:
        await database.dispose()


async def test_real_sqlite_applies_platform_region_risk_and_time_filters(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'retrieval-filters.db'}")
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    retriever = CorpusRetriever(repository)
    sources = (
        _source("matching", platform=Platform.EBAY, region="eu"),
        _source("wrong-platform", platform=Platform.AMAZON, region="eu"),
        _source("wrong-region", platform=Platform.EBAY, region="us"),
        _source("wrong-risk", platform=Platform.EBAY, region="eu"),
        _source("too-old", platform=Platform.EBAY, region="eu"),
    )
    try:
        await database.create_schema()
        await ingestion.sync_sources(sources)
        specifications = (
            ("matching", "f", RiskLevel.HIGH, NOW - timedelta(days=2)),
            ("wrong-platform", "g", RiskLevel.HIGH, NOW - timedelta(days=2)),
            ("wrong-region", "h", RiskLevel.HIGH, NOW - timedelta(days=2)),
            ("wrong-risk", "i", RiskLevel.LOW, NOW - timedelta(days=2)),
            ("too-old", "j", RiskLevel.HIGH, NOW - timedelta(days=31)),
        )
        for source_id, marker, risk, fetched_at in specifications:
            outcome = await ingestion.persist_version(
                _document(
                    source_id,
                    marker,
                    title=f"Policy {source_id}",
                    fetched_at=fetched_at,
                )
            )
            await _add_analysis(database, outcome.version_id, title=source_id, risk=risk)

        results = await retriever.search(
            CorpusQuery(
                text="policy",
                now=NOW,
                platforms=(Platform.EBAY,),
                regions=("eu",),
                risk_levels=(RiskLevel.HIGH,),
            )
        )

        assert [result.source_id for result in results] == ["matching"]
        assert results[0].published_at == NOW - timedelta(days=2)
    finally:
        await database.dispose()
