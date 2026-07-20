from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    Platform,
    RunStatus,
    RunSummary,
    SourceDefinition,
    Trigger,
    TrustTier,
)
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.ingestion import (
    PersistableDocument,
    SqlAlchemyIngestionRepository,
)
from commerce_agent.persistence.models import (
    Document,
    DocumentVersion,
    FetchRun,
    Source,
    SourceHealth,
    SourcePlatform,
)


def _source(
    *,
    name: str = "Amazon Seller News",
    platforms: tuple[Platform, ...] = (Platform.AMAZON, Platform.EBAY),
    enabled: bool = True,
) -> SourceDefinition:
    return SourceDefinition(
        source_id="amazon-news",
        name=name,
        entry_url="https://example.com/news",
        platforms=platforms,
        trust_tier=TrustTier.OFFICIAL,
        collector=CollectorKind.RSS,
        compliance=ComplianceStatus.ALLOWED,
        enabled=enabled,
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
    canonical_url: str = "https://example.com/news/fee-update",
    content_hash: str = "version-a",
    content_group_hash: str = "group-a",
    fetched_at: datetime | None = None,
) -> PersistableDocument:
    return PersistableDocument(
        source_id="amazon-news",
        canonical_url=canonical_url,
        title="Fee update",
        body="Amazon changed a seller fee.",
        language="en",
        language_confidence=0.99,
        content_hash=content_hash,
        content_group_hash=content_group_hash,
        fetched_at=fetched_at or datetime(2026, 7, 20, 1, tzinfo=UTC),
        author="Amazon",
        published_at=datetime(2026, 7, 19, 8, tzinfo=UTC),
        snapshot_path="2026/07/20/amazon-news/snapshot.bin.gz",
        etag='"etag-a"',
        last_modified="Sun, 19 Jul 2026 08:00:00 GMT",
    )


async def _repository(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'ingestion.db'}")
    await database.create_schema()
    return database, SqlAlchemyIngestionRepository(database.session)


async def test_sync_sources_upserts_definitions_and_replaces_platform_mapping(tmp_path) -> None:
    database, repository = await _repository(tmp_path)
    try:
        await repository.sync_sources([_source()])
        await repository.sync_sources(
            [_source(name="Renamed news", platforms=(Platform.TEMU,), enabled=False)]
        )

        async with database.session() as session:
            stored = await session.get(Source, "amazon-news")
            platforms = (
                await session.scalars(
                    select(SourcePlatform.platform).where(
                        SourcePlatform.source_id == "amazon-news"
                    )
                )
            ).all()

        assert stored is not None
        assert stored.name == "Renamed news"
        assert stored.enabled is False
        assert stored.regions == ["global"]
        assert stored.collector_config == {"item_limit": 50}
        assert platforms == [Platform.TEMU.value]
    finally:
        await database.dispose()


async def test_fetch_run_lifecycle_updates_health_aggregation(tmp_path) -> None:
    database, repository = await _repository(tmp_path)
    started_at = datetime(2026, 7, 20, 2, tzinfo=UTC)
    try:
        await repository.sync_sources([_source()])
        successful_run = await repository.start_run(
            "amazon-news", Trigger.SCHEDULED, started_at=started_at
        )
        await repository.finish_run(
            successful_run,
            RunSummary(
                source_id="amazon-news",
                trigger=Trigger.SCHEDULED,
                status=RunStatus.SUCCESS,
                started_at=started_at,
                finished_at=started_at + timedelta(minutes=1),
                discovered=4,
                created=2,
                updated=1,
                skipped=1,
                http_requests=3,
                http_not_modified=1,
                bytes_received=4096,
            ),
        )

        failed_started_at = started_at + timedelta(hours=2)
        failed_run = await repository.start_run(
            "amazon-news", Trigger.MANUAL, started_at=failed_started_at
        )
        await repository.finish_run(
            failed_run,
            RunSummary(
                source_id="amazon-news",
                trigger=Trigger.MANUAL,
                status=RunStatus.FAILED,
                started_at=failed_started_at,
                finished_at=failed_started_at + timedelta(seconds=15),
                failed=1,
                error_code="http_500",
                http_requests=2,
                bytes_received=512,
                error_summary="http_500",
            ),
        )

        async with database.session() as session:
            success = await session.get(FetchRun, successful_run)
            failure = await session.get(FetchRun, failed_run)
            health = await session.get(SourceHealth, "amazon-news")

        assert success is not None
        assert success.status == RunStatus.SUCCESS.value
        assert (success.discovered, success.created, success.updated, success.skipped) == (
            4,
            2,
            1,
            1,
        )
        assert (
            success.http_requests,
            success.http_not_modified,
            success.bytes_received,
        ) == (3, 1, 4096)
        assert success.error_summary is None
        assert failure is not None
        assert failure.status == RunStatus.FAILED.value
        assert (
            failure.http_requests,
            failure.http_not_modified,
            failure.bytes_received,
        ) == (2, 0, 512)
        assert failure.error_summary == "http_500"
        assert health is not None
        assert health.last_attempt_at == failed_started_at + timedelta(seconds=15)
        assert health.last_success_at == started_at + timedelta(minutes=1)
        assert health.consecutive_failures == 1
        assert health.last_error_code == "http_500"
        assert health.health_status == "error"
    finally:
        await database.dispose()


async def test_first_failed_run_initializes_health_failure_count(tmp_path) -> None:
    database, repository = await _repository(tmp_path)
    started_at = datetime(2026, 7, 20, 4, tzinfo=UTC)
    try:
        await repository.sync_sources([_source()])
        run_id = await repository.start_run(
            "amazon-news", Trigger.SCHEDULED, started_at=started_at
        )

        await repository.finish_run(
            run_id,
            RunSummary(
                source_id="amazon-news",
                trigger=Trigger.SCHEDULED,
                status=RunStatus.FAILED,
                started_at=started_at,
                finished_at=started_at + timedelta(seconds=10),
                failed=1,
                error_code="timeout",
            ),
        )

        async with database.session() as session:
            health = await session.get(SourceHealth, "amazon-news")

        assert health is not None
        assert health.consecutive_failures == 1
        assert health.health_status == "error"
    finally:
        await database.dispose()


async def test_first_partial_run_initializes_health_failure_count(tmp_path) -> None:
    database, repository = await _repository(tmp_path)
    started_at = datetime(2026, 7, 20, 5, tzinfo=UTC)
    try:
        await repository.sync_sources([_source()])
        run_id = await repository.start_run(
            "amazon-news", Trigger.MANUAL, started_at=started_at
        )

        await repository.finish_run(
            run_id,
            RunSummary(
                source_id="amazon-news",
                trigger=Trigger.MANUAL,
                status=RunStatus.PARTIAL,
                started_at=started_at,
                finished_at=started_at + timedelta(seconds=20),
                created=1,
                failed=1,
                error_code="extract_failed",
            ),
        )

        async with database.session() as session:
            health = await session.get(SourceHealth, "amazon-news")

        assert health is not None
        assert health.consecutive_failures == 1
        assert health.health_status == "degraded"
    finally:
        await database.dispose()


async def test_finish_run_preserves_the_started_at_recorded_by_start_run(tmp_path) -> None:
    database, repository = await _repository(tmp_path)
    persisted_started_at = datetime(2026, 7, 20, 6, tzinfo=UTC)
    summary_started_at = persisted_started_at + timedelta(minutes=5)
    try:
        await repository.sync_sources([_source()])
        run_id = await repository.start_run(
            "amazon-news", Trigger.SCHEDULED, started_at=persisted_started_at
        )

        await repository.finish_run(
            run_id,
            RunSummary(
                source_id="amazon-news",
                trigger=Trigger.SCHEDULED,
                status=RunStatus.SUCCESS,
                started_at=summary_started_at,
                finished_at=summary_started_at + timedelta(seconds=30),
            ),
        )

        async with database.session() as session:
            run = await session.get(FetchRun, run_id)

        assert run is not None
        assert run.started_at == persisted_started_at
    finally:
        await database.dispose()


async def test_persist_version_enforces_identity_and_immutable_version_uniqueness(
    tmp_path,
) -> None:
    database, repository = await _repository(tmp_path)
    try:
        await repository.sync_sources([_source()])
        first = await repository.persist_version(_candidate())
        duplicate = await repository.persist_version(_candidate())
        changed = await repository.persist_version(
            replace(
                _candidate(),
                body="Amazon changed the fee again.",
                content_hash="version-b",
                content_group_hash="group-b",
                fetched_at=datetime(2026, 7, 20, 3, tzinfo=UTC),
            )
        )
        duplicate_url = await repository.persist_version(
            _candidate(
                canonical_url="https://example.com/news/fee-update-copy",
                content_hash="copy-version",
                content_group_hash="group-b",
            )
        )
        stored = await repository.find_document(
            "amazon-news", "https://example.com/news/fee-update"
        )

        async with database.session() as session:
            document_count = await session.scalar(select(func.count()).select_from(Document))
            version_count = await session.scalar(
                select(func.count()).select_from(DocumentVersion)
            )
            grouped_document_count = await session.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.content_group_hash == "group-b")
            )

        assert first.created_document is True
        assert first.created_version is True
        assert duplicate.document_id == first.document_id
        assert duplicate.version_id == first.version_id
        assert duplicate.created_document is False
        assert duplicate.created_version is False
        assert changed.document_id == first.document_id
        assert changed.created_version is True
        assert duplicate_url.document_id != first.document_id
        assert stored is not None
        assert stored.current_version_id == changed.version_id
        assert stored.content_group_hash == "group-b"
        assert document_count == 2
        assert version_count == 3
        assert grouped_document_count == 2
    finally:
        await database.dispose()


async def test_concurrent_duplicate_version_inserts_converge_without_integrity_error(
    tmp_path,
) -> None:
    database, repository = await _repository(tmp_path)
    competing_repository = SqlAlchemyIngestionRepository(database.session)
    try:
        await repository.sync_sources([_source()])

        outcomes = await asyncio.gather(
            repository.persist_version(_candidate()),
            competing_repository.persist_version(_candidate()),
        )

        async with database.session() as session:
            document_count = await session.scalar(select(func.count()).select_from(Document))
            version_count = await session.scalar(
                select(func.count()).select_from(DocumentVersion)
            )

        assert {outcome.document_id for outcome in outcomes} == {outcomes[0].document_id}
        assert {outcome.version_id for outcome in outcomes} == {outcomes[0].version_id}
        assert sum(outcome.created_document for outcome in outcomes) == 1
        assert sum(outcome.created_version for outcome in outcomes) == 1
        assert document_count == 1
        assert version_count == 1
    finally:
        await database.dispose()
