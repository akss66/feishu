from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import case, exists, literal, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from commerce_agent.ingestion.models import Platform, TrustTier
from commerce_agent.intelligence.models import AnalysisCandidate, AnalysisResult
from commerce_agent.persistence.models import (
    AnalysisJob,
    Document,
    DocumentAnalysis,
    DocumentVersion,
    Source,
    SourcePlatform,
    UTCDateTime,
)


class StaleLeaseError(RuntimeError):
    pass


class SqlAlchemyIntelligenceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim_next(
        self, *, now: datetime, lease_seconds: int = 300
    ) -> AnalysisCandidate | None:
        lease_token = uuid4().hex
        due = or_(
            AnalysisJob.status == "pending",
            (AnalysisJob.status == "retry_wait") & (AnalysisJob.next_attempt_at <= now),
            (AnalysisJob.status == "running") & (AnalysisJob.lease_expires_at <= now),
        )
        next_job_id = (
            select(AnalysisJob.id)
            .where(due)
            .order_by(AnalysisJob.created_at, AnalysisJob.id)
            .limit(1)
            .scalar_subquery()
        )
        claim = (
            update(AnalysisJob)
            .where(AnalysisJob.id == next_job_id)
            .values(
                status="running",
                attempt_count=AnalysisJob.attempt_count + 1,
                next_attempt_at=None,
                lease_token=lease_token,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                error_code=None,
                updated_at=now,
            )
            .returning(AnalysisJob.id)
        )

        async with self._session_factory.begin() as session:
            job_id = (await session.execute(claim)).scalar_one_or_none()
            if job_id is None:
                return None
            return await self._load_candidate(session, job_id)

    async def complete_analysis(
        self,
        claim: AnalysisCandidate,
        result: AnalysisResult,
        evidence_confidence: int,
        event_fingerprint: str,
        *,
        now: datetime,
        model_name: str,
        schema_version: str = "1",
        prompt_version: str = "1",
    ) -> int:
        lease_token = _require_lease_token(claim)
        async with self._session_factory.begin() as session:
            completed_job_id = (
                await session.execute(
                    update(AnalysisJob)
                    .where(
                        AnalysisJob.id == claim.job_id,
                        AnalysisJob.lease_token == lease_token,
                        AnalysisJob.status == "running",
                    )
                    .values(
                        status="completed",
                        next_attempt_at=None,
                        lease_token=None,
                        lease_expires_at=None,
                        error_code=None,
                        updated_at=now,
                    )
                    .returning(AnalysisJob.id)
                )
            ).scalar_one_or_none()
            if completed_job_id is None:
                raise StaleLeaseError("analysis lease is no longer current")

            analysis = DocumentAnalysis(
                document_version_id=claim.document_version_id,
                schema_version=schema_version,
                prompt_version=prompt_version,
                model_name=model_name,
                headline_zh=result.headline_zh,
                summary_zh=result.summary_zh,
                event_type=result.event_type.value,
                risk_level=result.risk_level.value,
                evidence_confidence=evidence_confidence,
                event_fingerprint=event_fingerprint,
                structured_payload=result.model_dump(mode="json"),
                analyzed_at=now,
            )
            session.add(analysis)
            await session.flush()
            return analysis.id

    async def fail_analysis(
        self, claim: AnalysisCandidate, error_code: str, *, now: datetime
    ) -> None:
        lease_token = _require_lease_token(claim)
        should_retry = AnalysisJob.attempt_count < 2
        async with self._session_factory.begin() as session:
            failed_job_id = (
                await session.execute(
                    update(AnalysisJob)
                    .where(
                        AnalysisJob.id == claim.job_id,
                        AnalysisJob.lease_token == lease_token,
                        AnalysisJob.status == "running",
                    )
                    .values(
                        status=case((should_retry, "retry_wait"), else_="failed"),
                        next_attempt_at=case(
                            (should_retry, now + timedelta(minutes=5)), else_=None
                        ),
                        lease_token=None,
                        lease_expires_at=None,
                        error_code=error_code,
                        updated_at=now,
                    )
                    .returning(AnalysisJob.id)
                )
            ).scalar_one_or_none()
            if failed_job_id is None:
                raise StaleLeaseError("analysis lease is no longer current")

    async def backfill_jobs(self, *, limit: int) -> int:
        if limit <= 0:
            return 0

        now = datetime.now(UTC)
        missing_versions = (
            select(
                DocumentVersion.id,
                literal("pending"),
                literal(0),
                literal(now, type_=UTCDateTime()),
                literal(now, type_=UTCDateTime()),
            )
            .where(
                ~exists(
                    select(AnalysisJob.id).where(
                        AnalysisJob.document_version_id == DocumentVersion.id
                    )
                )
            )
            .order_by(DocumentVersion.id)
            .limit(limit)
        )
        statement = (
            sqlite_insert(AnalysisJob)
            .from_select(
                [
                    "document_version_id",
                    "status",
                    "attempt_count",
                    "created_at",
                    "updated_at",
                ],
                missing_versions,
            )
            .on_conflict_do_nothing(index_elements=["document_version_id"])
            .returning(AnalysisJob.id)
        )
        async with self._session_factory.begin() as session:
            inserted_ids = (await session.scalars(statement)).all()
            return len(inserted_ids)

    async def _load_candidate(
        self, session: AsyncSession, job_id: int
    ) -> AnalysisCandidate:
        row = (
            await session.execute(
                select(AnalysisJob, DocumentVersion, Document, Source)
                .join(
                    DocumentVersion,
                    DocumentVersion.id == AnalysisJob.document_version_id,
                )
                .join(Document, Document.id == DocumentVersion.document_id)
                .join(Source, Source.id == Document.source_id)
                .where(AnalysisJob.id == job_id)
            )
        ).one()
        job, version, document, source = row
        platforms = tuple(
            Platform(platform)
            for platform in (
                await session.scalars(
                    select(SourcePlatform.platform)
                    .where(SourcePlatform.source_id == source.id)
                    .order_by(SourcePlatform.platform)
                )
            ).all()
        )
        return AnalysisCandidate(
            job_id=job.id,
            lease_token=job.lease_token,
            document_version_id=version.id,
            source_id=source.id,
            source_name=source.name,
            trust_tier=TrustTier(source.trust_tier),
            canonical_url=document.canonical_url,
            content_hash=version.content_hash,
            title=version.title,
            body=version.body,
            language=version.language,
            language_confidence=version.language_confidence,
            author=version.author,
            published_at=version.published_at,
            fetched_at=version.fetched_at,
            platforms=platforms,
            regions=tuple(source.regions),
        )


def _require_lease_token(claim: AnalysisCandidate) -> str:
    if claim.lease_token is None:
        raise StaleLeaseError("analysis claim has no lease token")
    return claim.lease_token
