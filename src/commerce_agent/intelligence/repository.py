from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import case, exists, func, literal, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from commerce_agent.ingestion.models import Platform, TrustTier
from commerce_agent.intelligence.models import (
    AnalysisCandidate,
    AnalysisResult,
    MessageKind,
    RiskLevel,
    RiskResolution,
    ScoredAnalysis,
)
from commerce_agent.intelligence.reports import (
    CoverageRow,
    DailyReportDraft,
    ReportAlreadySent,
)
from commerce_agent.persistence.models import (
    AnalysisJob,
    DailyReport,
    DeliveryOutbox,
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
            (AnalysisJob.status == "running")
            & (AnalysisJob.lease_expires_at <= now)
            & (AnalysisJob.attempt_count < 2),
        )
        next_job_id = (
            select(AnalysisJob.id)
            .join(
                DocumentVersion,
                DocumentVersion.id == AnalysisJob.document_version_id,
            )
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(Source, Source.id == Document.source_id)
            .where(due)
            .where(Source.compliance == "allowed")
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
            await session.execute(
                update(AnalysisJob)
                .where(
                    AnalysisJob.status == "running",
                    AnalysisJob.lease_expires_at <= now,
                    AnalysisJob.attempt_count >= 2,
                )
                .values(
                    status="failed",
                    next_attempt_at=None,
                    lease_token=None,
                    lease_expires_at=None,
                    error_code="lease_expired",
                    updated_at=now,
                )
            )
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
        risk_level: RiskLevel | None = None,
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
                risk_level=(risk_level or result.risk_level).value,
                evidence_confidence=evidence_confidence,
                event_fingerprint=event_fingerprint,
                structured_payload=result.model_dump(mode="json"),
                analyzed_at=now,
            )
            session.add(analysis)
            await session.flush()
            return analysis.id

    async def count_corroborating_sources(
        self,
        fingerprint: str,
        claim: AnalysisCandidate,
        *,
        batch_claims: tuple[AnalysisCandidate, ...] = (),
    ) -> int:
        candidate_version_ids = {
            candidate.document_version_id for candidate in (claim, *batch_claims)
        }
        async with self._session_factory() as session:
            persisted_source_ids = set(
                await session.scalars(
                    select(func.distinct(Document.source_id))
                    .select_from(DocumentAnalysis)
                    .join(
                        DocumentVersion,
                        DocumentVersion.id == DocumentAnalysis.document_version_id,
                    )
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .join(Source, Source.id == Document.source_id)
                    .join(
                        AnalysisJob,
                        AnalysisJob.document_version_id == DocumentVersion.id,
                    )
                    .where(
                        DocumentAnalysis.event_fingerprint == fingerprint,
                        Document.current_version_id == DocumentVersion.id,
                        Source.compliance == "allowed",
                        AnalysisJob.status == "completed",
                    )
                )
            )
            batch_source_ids = set(
                await session.scalars(
                    select(func.distinct(Document.source_id))
                    .select_from(DocumentVersion)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .join(Source, Source.id == Document.source_id)
                    .where(
                        DocumentVersion.id.in_(candidate_version_ids),
                        Document.current_version_id == DocumentVersion.id,
                        Source.compliance == "allowed",
                    )
                )
            )
        return len(persisted_source_ids | batch_source_ids)

    async def list_analyses(self) -> list[DocumentAnalysis]:
        async with self._session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(DocumentAnalysis).order_by(DocumentAnalysis.id)
                    )
                ).all()
            )

    async def list_report_analyses(
        self, *, window_start: datetime, window_end: datetime
    ) -> tuple[ScoredAnalysis, ...]:
        statement = (
            select(DocumentAnalysis, AnalysisJob, DocumentVersion, Document, Source)
            .join(
                DocumentVersion,
                DocumentVersion.id == DocumentAnalysis.document_version_id,
            )
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(Source, Source.id == Document.source_id)
            .join(
                AnalysisJob,
                AnalysisJob.document_version_id == DocumentVersion.id,
            )
            .where(
                Document.current_version_id == DocumentVersion.id,
                Source.compliance == "allowed",
                DocumentAnalysis.evidence_confidence >= 60,
                DocumentVersion.fetched_at >= window_start,
                DocumentVersion.fetched_at < window_end,
            )
            .order_by(DocumentVersion.fetched_at.desc(), DocumentAnalysis.id.desc())
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
            if not rows:
                return ()
            platform_rows = (
                await session.execute(
                    select(SourcePlatform.source_id, SourcePlatform.platform)
                    .where(
                        SourcePlatform.source_id.in_(
                            {source.id for _, _, _, _, source in rows}
                        )
                    )
                    .order_by(SourcePlatform.source_id, SourcePlatform.platform)
                )
            ).all()
            platforms_by_source: dict[str, list[Platform]] = {}
            for source_id, platform in platform_rows:
                platforms_by_source.setdefault(source_id, []).append(Platform(platform))
            analyses: list[ScoredAnalysis] = []
            for analysis, job, version, document, source in rows:
                candidate = AnalysisCandidate(
                    job_id=job.id,
                    lease_token=None,
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
                    platforms=tuple(platforms_by_source.get(source.id, ())),
                    regions=tuple(source.regions),
                )
                risk = RiskLevel(analysis.risk_level)
                analyses.append(
                    ScoredAnalysis(
                        analysis_id=analysis.id,
                        candidate=candidate,
                        result=AnalysisResult.model_validate(analysis.structured_payload),
                        evidence_confidence=analysis.evidence_confidence,
                        resolution=RiskResolution(
                            risk_level=risk,
                            rule_hits=(),
                            needs_review=False,
                        ),
                        event_fingerprint=analysis.event_fingerprint,
                    )
                )
            return tuple(analyses)

    async def list_coverage(
        self, *, window_start: datetime, window_end: datetime
    ) -> tuple[CoverageRow, ...]:
        async with self._session_factory() as session:
            enabled_counts = dict(
                (
                    await session.execute(
                        select(SourcePlatform.platform, func.count(Source.id.distinct()))
                        .join(Source, Source.id == SourcePlatform.source_id)
                        .where(Source.compliance == "allowed", Source.enabled.is_(True))
                        .group_by(SourcePlatform.platform)
                    )
                ).all()
            )
            verified_counts = dict(
                (
                    await session.execute(
                        select(
                            SourcePlatform.platform,
                            func.count(DocumentAnalysis.event_fingerprint.distinct()),
                        )
                        .select_from(DocumentAnalysis)
                        .join(
                            DocumentVersion,
                            DocumentVersion.id == DocumentAnalysis.document_version_id,
                        )
                        .join(Document, Document.id == DocumentVersion.document_id)
                        .join(Source, Source.id == Document.source_id)
                        .join(SourcePlatform, SourcePlatform.source_id == Source.id)
                        .where(
                            Document.current_version_id == DocumentVersion.id,
                            Source.compliance == "allowed",
                            Source.enabled.is_(True),
                            DocumentAnalysis.evidence_confidence >= 75,
                            DocumentVersion.fetched_at >= window_start,
                            DocumentVersion.fetched_at < window_end,
                        )
                        .group_by(SourcePlatform.platform)
                    )
                ).all()
            )
        return tuple(
            CoverageRow(
                platform=platform,
                enabled_source_count=enabled_counts.get(platform.value, 0),
                verified_update_count=verified_counts.get(platform.value, 0),
            )
            for platform in Platform
        )

    async def save_report(
        self, group_id: str, draft: DailyReportDraft, *, now: datetime
    ) -> int:
        values = {
            "group_id": group_id,
            "report_date": draft.report_date,
            "window_start": draft.window_start,
            "window_end": draft.window_end,
            "status": "draft",
            "selected_analysis_ids": list(draft.selected_analysis_ids),
            "report_payload": draft.payload,
            "created_at": now,
        }
        async with self._session_factory.begin() as session:
            inserted_id = (
                await session.execute(
                    sqlite_insert(DailyReport)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=["group_id", "report_date"])
                    .returning(DailyReport.id)
                )
            ).scalar_one_or_none()
            if inserted_id is not None:
                return inserted_id

            report = (
                await session.execute(
                    select(DailyReport).where(
                        DailyReport.group_id == group_id,
                        DailyReport.report_date == draft.report_date,
                    )
                )
            ).scalar_one()
            if report.status == "sent":
                raise ReportAlreadySent(
                    f"daily report already sent for {group_id} on {draft.report_date}"
                )
            if report.status == "queued":
                if (
                    report.window_start == draft.window_start
                    and report.window_end == draft.window_end
                    and report.selected_analysis_ids
                    == list(draft.selected_analysis_ids)
                    and report.report_payload == draft.payload
                ):
                    return report.id
                raise RuntimeError(
                    f"daily report already queued for {group_id} on {draft.report_date}"
                )
            report.window_start = draft.window_start
            report.window_end = draft.window_end
            report.selected_analysis_ids = list(draft.selected_analysis_ids)
            report.report_payload = draft.payload
            return report.id

    async def mark_report_previewed(self, report_id: int) -> None:
        async with self._session_factory.begin() as session:
            report = await session.get(DailyReport, report_id)
            if report is None:
                raise KeyError(f"daily report {report_id} does not exist")
            if report.status == "draft":
                report.status = "previewed"

    async def get_report_id(self, group_id: str, report_date: date) -> int:
        async with self._session_factory() as session:
            report_id = await session.scalar(
                select(DailyReport.id).where(
                    DailyReport.group_id == group_id,
                    DailyReport.report_date == report_date,
                )
            )
        if report_id is None:
            raise KeyError(f"daily report does not exist for {group_id} on {report_date}")
        return report_id

    async def queue_report(self, report_id: int, *, now: datetime) -> int:
        async with self._session_factory.begin() as session:
            report = await session.get(DailyReport, report_id)
            if report is None:
                raise KeyError(f"daily report {report_id} does not exist")
            if report.status == "sent":
                raise ReportAlreadySent(
                    f"daily report {report_id} has already been sent"
                )
            if report.status not in {"previewed", "queued"}:
                raise RuntimeError("daily report must be previewed before it can be queued")

            idempotency_key = f"daily:{report.group_id}:{report.report_date.isoformat()}"
            existing_id = await session.scalar(
                select(DeliveryOutbox.id).where(
                    DeliveryOutbox.idempotency_key == idempotency_key
                )
            )
            if existing_id is not None:
                if report.status == "previewed":
                    report.status = "queued"
                return existing_id
            if report.status == "queued":
                raise RuntimeError("queued daily report is missing its outbox row")

            outbox_id = (
                await session.execute(
                    sqlite_insert(DeliveryOutbox)
                    .values(
                        idempotency_key=idempotency_key,
                        group_id=report.group_id,
                        message_kind=MessageKind.DAILY_REPORT.value,
                        payload=report.report_payload,
                        reply_to_message_id=None,
                        reply_in_thread=False,
                        status="pending",
                        attempt_count=0,
                        created_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["idempotency_key"])
                    .returning(DeliveryOutbox.id)
                )
            ).scalar_one_or_none()
            if outbox_id is None:
                outbox_id = (
                    await session.execute(
                        select(DeliveryOutbox.id).where(
                            DeliveryOutbox.idempotency_key == idempotency_key
                        )
                    )
                ).scalar_one()
            report.status = "queued"
            return outbox_id

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
