from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from commerce_agent.ingestion.models import (
    RunStatus,
    RunSummary,
    SourceDefinition,
    Trigger,
)
from commerce_agent.persistence.models import (
    AnalysisJob,
    Document,
    DocumentAnalysis,
    DocumentProvenance,
    DocumentVersion,
    FetchRun,
    OfficialNoticeAudit,
    Source,
    SourceHealth,
    SourceLease,
    SourceMaterialPolicy,
    SourcePlatform,
)

SOURCE_LEASE_TTL = timedelta(hours=24)
SOURCE_FAILURE_THRESHOLD: Final[int] = 3
EXPIRED_MEDIA_BODY: Final[str] = (
    "[media body expired; use analysis evidence and original link]"
)


@dataclass(frozen=True, slots=True)
class PersistableDocument:
    source_id: str
    canonical_url: str
    title: str
    body: str
    language: str
    language_confidence: float
    content_hash: str
    content_group_hash: str
    fetched_at: datetime
    author: str | None = None
    published_at: datetime | None = None
    snapshot_path: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    publisher_key: str | None = None
    attribution: str | None = None
    content_scope: str | None = None

    def __post_init__(self) -> None:
        provenance = (self.publisher_key, self.attribution, self.content_scope)
        if any(value is not None for value in provenance):
            if any(not isinstance(value, str) or not value.strip() for value in provenance):
                raise ValueError("media provenance must be complete")
            if self.content_scope not in {
                "metadata_only",
                "feed_summary",
                "full_text",
            }:
                raise ValueError("unsupported media content scope")


@dataclass(frozen=True, slots=True)
class StoredDocument:
    id: int
    source_id: str
    canonical_url: str
    first_seen_at: datetime
    last_seen_at: datetime
    current_version_id: int | None
    content_group_hash: str


@dataclass(frozen=True, slots=True)
class PersistOutcome:
    document_id: int
    version_id: int
    created_document: bool
    created_version: bool


class IngestionRepository(Protocol):
    async def sync_sources(self, sources: Sequence[SourceDefinition]) -> None: ...

    async def is_source_suspended(self, source_id: str) -> bool: ...

    async def claim_source(
        self,
        source_id: str,
        *,
        acquired_at: datetime | None = None,
    ) -> str | None: ...

    async def release_source(self, source_id: str, lease_token: str) -> None: ...

    async def start_run(
        self,
        source_id: str,
        trigger: Trigger,
        *,
        started_at: datetime | None = None,
    ) -> int: ...

    async def find_document(
        self, source_id: str, canonical_url: str
    ) -> StoredDocument | None: ...

    async def persist_version(self, candidate: PersistableDocument) -> PersistOutcome: ...

    async def redact_expired_media_bodies(
        self,
        *,
        source_ids: Sequence[str],
        before: datetime,
    ) -> int: ...

    async def finish_run(self, run_id: int, summary: RunSummary) -> None: ...


class SqlAlchemyIngestionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def is_source_suspended(self, source_id: str) -> bool:
        async with self._session_factory() as session:
            status = await session.scalar(
                select(SourceHealth.health_status).where(
                    SourceHealth.source_id == source_id
                )
            )
        return status == "suspended"

    async def record_official_notice_audit(
        self,
        *,
        audit_id: str,
        transport: str,
        source_account: str,
        platform: str,
        submitted_by_hash: str,
        original_url_hash: str,
        status: str,
        error_code: str | None,
        received_at: datetime,
    ) -> None:
        async with self._session_factory.begin() as session:
            session.add(
                OfficialNoticeAudit(
                    audit_id=audit_id,
                    transport=transport,
                    source_account=source_account,
                    platform=platform,
                    submitted_by_hash=submitted_by_hash,
                    original_url_hash=original_url_hash,
                    status=status,
                    error_code=error_code,
                    received_at=received_at,
                )
            )

    async def sync_sources(self, sources: Sequence[SourceDefinition]) -> None:
        now = datetime.now(UTC)
        async with self._session_factory.begin() as session:
            for definition in sources:
                source = await session.get(Source, definition.source_id)
                values = _source_values(definition)
                if source is None:
                    session.add(Source(id=definition.source_id, created_at=now, **values))
                    await session.flush()
                else:
                    for field, value in values.items():
                        setattr(source, field, value)
                    source.updated_at = now

                await session.execute(
                    delete(SourcePlatform).where(
                        SourcePlatform.source_id == definition.source_id
                    )
                )
                session.add_all(
                    SourcePlatform(source_id=definition.source_id, platform=platform.value)
                    for platform in definition.platforms
                )
                policy_values = (
                    definition.publisher_key,
                    definition.attribution,
                    definition.content_scope,
                )
                if all(value is not None for value in policy_values):
                    policy = await session.get(SourceMaterialPolicy, definition.source_id)
                    if policy is None:
                        session.add(
                            SourceMaterialPolicy(
                                source_id=definition.source_id,
                                publisher_key=definition.publisher_key,
                                attribution=definition.attribution,
                                content_scope=definition.content_scope.value,
                            )
                        )
                    else:
                        policy.publisher_key = definition.publisher_key
                        policy.attribution = definition.attribution
                        policy.content_scope = definition.content_scope.value
                else:
                    await session.execute(
                        delete(SourceMaterialPolicy).where(
                            SourceMaterialPolicy.source_id == definition.source_id
                        )
                    )

    async def claim_source(
        self,
        source_id: str,
        *,
        acquired_at: datetime | None = None,
    ) -> str | None:
        claimed_at = acquired_at or datetime.now(UTC)
        lease_token = uuid4().hex
        async with self._session_factory.begin() as session:
            await session.execute(
                delete(SourceLease).where(
                    SourceLease.source_id == source_id,
                    SourceLease.acquired_at <= claimed_at - SOURCE_LEASE_TTL,
                )
            )
            result = await session.execute(
                sqlite_insert(SourceLease)
                .values(
                    source_id=source_id,
                    lease_token=lease_token,
                    acquired_at=claimed_at,
                )
                .on_conflict_do_nothing(index_elements=["source_id"])
            )
            return lease_token if result.rowcount == 1 else None

    async def release_source(self, source_id: str, lease_token: str) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                delete(SourceLease).where(
                    SourceLease.source_id == source_id,
                    SourceLease.lease_token == lease_token,
                )
            )

    async def start_run(
        self,
        source_id: str,
        trigger: Trigger,
        *,
        started_at: datetime | None = None,
    ) -> int:
        async with self._session_factory.begin() as session:
            run = FetchRun(
                source_id=source_id,
                trigger=trigger.value,
                status="running",
                started_at=started_at or datetime.now(UTC),
            )
            session.add(run)
            await session.flush()
            return run.id

    async def find_document(
        self, source_id: str, canonical_url: str
    ) -> StoredDocument | None:
        async with self._session_factory() as session:
            document = await session.scalar(
                select(Document).where(
                    Document.source_id == source_id,
                    Document.canonical_url == canonical_url,
                )
            )
            return _stored_document(document) if document is not None else None

    async def persist_version(self, candidate: PersistableDocument) -> PersistOutcome:
        async with self._session_factory.begin() as session:
            document_insert = (
                sqlite_insert(Document)
                .values(
                    source_id=candidate.source_id,
                    canonical_url=candidate.canonical_url,
                    first_seen_at=candidate.fetched_at,
                    last_seen_at=candidate.fetched_at,
                    content_group_hash=candidate.content_group_hash,
                )
                .on_conflict_do_nothing(index_elements=["source_id", "canonical_url"])
            )
            document_result = await session.execute(document_insert)
            created_document = document_result.rowcount == 1
            document = await session.scalar(
                select(Document).where(
                    Document.source_id == candidate.source_id,
                    Document.canonical_url == candidate.canonical_url,
                )
            )
            if document is None:  # pragma: no cover - guarded by the unique insert above
                raise RuntimeError("document insert did not produce a stored document")

            version_insert = (
                sqlite_insert(DocumentVersion)
                .values(
                    document_id=document.id,
                    title=candidate.title,
                    body=candidate.body,
                    language=candidate.language,
                    language_confidence=candidate.language_confidence,
                    author=candidate.author,
                    published_at=candidate.published_at,
                    content_hash=candidate.content_hash,
                    snapshot_path=candidate.snapshot_path,
                    etag=candidate.etag,
                    last_modified=candidate.last_modified,
                    fetched_at=candidate.fetched_at,
                )
                .on_conflict_do_nothing(index_elements=["document_id", "content_hash"])
            )
            version_result = await session.execute(version_insert)
            created_version = version_result.rowcount == 1
            version_id = await session.scalar(
                select(DocumentVersion.id).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.content_hash == candidate.content_hash,
                )
            )
            if version_id is None:  # pragma: no cover - guarded by the unique insert above
                raise RuntimeError("version insert did not produce a stored version")

            if candidate.publisher_key is not None:
                provenance = await session.get(DocumentProvenance, version_id)
                candidate_provenance = (
                    candidate.publisher_key,
                    candidate.attribution,
                    candidate.content_scope,
                )
                if provenance is None:
                    session.add(
                        DocumentProvenance(
                            document_version_id=version_id,
                            publisher_key=candidate.publisher_key,
                            attribution=candidate.attribution,
                            content_scope=candidate.content_scope,
                        )
                    )
                elif (
                    provenance.publisher_key,
                    provenance.attribution,
                    provenance.content_scope,
                ) != candidate_provenance:
                    raise ValueError("document_provenance_conflict")

            if created_version:
                now = datetime.now(UTC)
                await session.execute(
                    sqlite_insert(AnalysisJob)
                    .values(
                        document_version_id=version_id,
                        status="pending",
                        attempt_count=0,
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["document_version_id"])
                )

            if candidate.fetched_at >= document.last_seen_at:
                document.last_seen_at = candidate.fetched_at
                document.current_version_id = version_id
                document.content_group_hash = candidate.content_group_hash

            return PersistOutcome(
                document_id=document.id,
                version_id=version_id,
                created_document=created_document,
                created_version=created_version,
            )

    async def redact_expired_media_bodies(
        self,
        *,
        source_ids: Sequence[str],
        before: datetime,
    ) -> int:
        if not source_ids:
            return 0
        candidate_ids = (
            select(DocumentVersion.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(
                DocumentProvenance,
                DocumentProvenance.document_version_id == DocumentVersion.id,
            )
            .join(
                DocumentAnalysis,
                DocumentAnalysis.document_version_id == DocumentVersion.id,
            )
            .where(
                Document.source_id.in_(tuple(source_ids)),
                DocumentProvenance.content_scope == "full_text",
                DocumentVersion.fetched_at < before,
                DocumentVersion.body != EXPIRED_MEDIA_BODY,
            )
        )
        async with self._session_factory.begin() as session:
            result = await session.execute(
                update(DocumentVersion)
                .where(DocumentVersion.id.in_(candidate_ids))
                .values(body=EXPIRED_MEDIA_BODY, snapshot_path=None)
            )
            return result.rowcount

    async def finish_run(self, run_id: int, summary: RunSummary) -> None:
        async with self._session_factory.begin() as session:
            run = await session.get(FetchRun, run_id)
            if run is None:
                raise KeyError(f"unknown fetch run '{run_id}'")
            if run.source_id != summary.source_id or run.trigger != summary.trigger.value:
                raise ValueError("run summary does not match the started fetch run")

            run.status = summary.status.value
            run.finished_at = summary.finished_at
            run.discovered = summary.discovered
            run.created = summary.created
            run.updated = summary.updated
            run.skipped = summary.skipped
            run.failed = summary.failed
            run.error_code = summary.error_code
            run.http_requests = summary.http_requests
            run.http_not_modified = summary.http_not_modified
            run.bytes_received = summary.bytes_received
            run.error_summary = summary.error_summary

            source = await session.get(Source, summary.source_id)
            if source is None:  # pragma: no cover - the run foreign key guarantees this
                raise RuntimeError("fetch run references an unknown source")
            health = await session.get(SourceHealth, summary.source_id)
            if health is None:
                health = SourceHealth(
                    source_id=summary.source_id,
                    consecutive_failures=0,
                    health_status="unknown",
                )
                session.add(health)

            health.last_attempt_at = summary.finished_at
            health.next_scheduled_at = summary.finished_at + timedelta(
                minutes=source.interval_minutes
            )
            if summary.status is RunStatus.SUCCESS:
                health.last_success_at = summary.finished_at
                health.consecutive_failures = 0
                health.last_error_code = None
                health.health_status = "healthy"
            elif summary.status is RunStatus.SKIPPED:
                if health.health_status != "suspended":
                    health.health_status = "healthy"
            else:
                health.consecutive_failures += 1
                health.last_error_code = summary.error_code
                health.health_status = "suspended" if (
                    health.consecutive_failures >= SOURCE_FAILURE_THRESHOLD
                ) else ("degraded" if summary.status is RunStatus.PARTIAL else "error")


def _source_values(source: SourceDefinition) -> dict[str, object]:
    return {
        "name": source.name,
        "entry_url": source.entry_url,
        "trust_tier": source.trust_tier.value,
        "collector": source.collector.value,
        "compliance": source.compliance.value,
        "enabled": source.enabled,
        "regions": list(source.regions),
        "language_hint": source.language_hint,
        "interval_minutes": source.interval_minutes,
        "collector_config": dict(source.collector_config),
        "terms_url": source.terms_url,
        "robots_url": source.robots_url,
        "reviewed_at": source.reviewed_at,
        "compliance_notes": source.compliance_notes,
        "updated_at": datetime.now(UTC),
    }


def _stored_document(document: Document) -> StoredDocument:
    return StoredDocument(
        id=document.id,
        source_id=document.source_id,
        canonical_url=document.canonical_url,
        first_seen_at=document.first_seen_at,
        last_seen_at=document.last_seen_at,
        current_version_id=document.current_version_id,
        content_group_hash=document.content_group_hash,
    )
