from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import case, exists, func, literal, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from commerce_agent.ingestion.models import Platform, TrustTier
from commerce_agent.intelligence.models import (
    AnalysisCandidate,
    AnalysisResult,
    DeliveryClaim,
    DeliveryMessage,
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


RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
)
_ALERT_KINDS = {MessageKind.HIGH_ALERT.value, MessageKind.MEDIUM_ALERT_BATCH.value}
_RISK_ORDER = {RiskLevel.LOW.value: 0, RiskLevel.MEDIUM.value: 1, RiskLevel.HIGH.value: 2}


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
            return tuple(
                _scored_analysis(
                    analysis,
                    job,
                    version,
                    document,
                    source,
                    platforms=tuple(platforms_by_source.get(source.id, ())),
                )
                for analysis, job, version, document, source in rows
            )

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

    async def queue_alerts(
        self,
        messages: tuple[DeliveryMessage, ...],
        *,
        now: datetime,
        dedup_hours: int,
    ) -> tuple[int, ...]:
        if not messages:
            return ()
        if dedup_hours <= 0:
            raise ValueError("dedup_hours must be positive")

        queued_ids: list[int] = []
        async with self._session_factory.begin() as session:
            recent_rows = list(
                (
                    await session.scalars(
                        select(DeliveryOutbox)
                        .where(
                            DeliveryOutbox.group_id.in_(
                                {message.group_id for message in messages}
                            ),
                            DeliveryOutbox.message_kind.in_(_ALERT_KINDS),
                            DeliveryOutbox.created_at > now - timedelta(hours=dedup_hours),
                            DeliveryOutbox.created_at <= now,
                        )
                        .order_by(DeliveryOutbox.id)
                    )
                ).all()
            )
            recent_items_by_group: dict[str, list[dict[str, object]]] = {}
            for row in recent_rows:
                recent_items_by_group.setdefault(row.group_id, []).extend(
                    _payload_items(row.payload)
                )

            for message in messages:
                recent_items = recent_items_by_group.setdefault(message.group_id, [])
                new_items = [
                    item
                    for item in _payload_items(message.payload)
                    if _alert_item_allowed(item, recent_items)
                ]
                if _payload_items(message.payload) and not new_items:
                    continue
                queued_message = _with_alert_items(message, new_items)
                inserted_id = (
                    await session.execute(
                        sqlite_insert(DeliveryOutbox)
                        .values(
                            idempotency_key=queued_message.idempotency_key,
                            group_id=queued_message.group_id,
                            message_kind=queued_message.kind.value,
                            payload=queued_message.payload,
                            reply_to_message_id=queued_message.reply_to_message_id,
                            reply_in_thread=queued_message.reply_in_thread,
                            status="pending",
                            attempt_count=0,
                            created_at=now,
                        )
                        .on_conflict_do_nothing(index_elements=["idempotency_key"])
                        .returning(DeliveryOutbox.id)
                    )
                ).scalar_one_or_none()
                if inserted_id is None:
                    continue
                queued_ids.append(inserted_id)
                recent_items.extend(new_items)
        return tuple(queued_ids)

    async def list_unqueued_alert_candidates(
        self, *, since: datetime, until: datetime
    ) -> tuple[ScoredAnalysis, ...]:
        statement = (
            select(DocumentAnalysis, AnalysisJob, DocumentVersion, Document, Source)
            .join(
                DocumentVersion,
                DocumentVersion.id == DocumentAnalysis.document_version_id,
            )
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(Source, Source.id == Document.source_id)
            .join(AnalysisJob, AnalysisJob.document_version_id == DocumentVersion.id)
            .where(
                AnalysisJob.status == "completed",
                Document.current_version_id == DocumentVersion.id,
                Source.compliance == "allowed",
                DocumentAnalysis.analyzed_at >= since,
                DocumentAnalysis.analyzed_at <= until,
            )
            .order_by(DocumentAnalysis.analyzed_at, DocumentAnalysis.id)
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

            return tuple(
                _scored_analysis(
                    analysis,
                    job,
                    version,
                    document,
                    source,
                    platforms=tuple(platforms_by_source.get(source.id, ())),
                )
                for analysis, job, version, document, source in rows
            )

    async def claim_delivery(
        self, *, now: datetime, lease_seconds: int = 300
    ) -> DeliveryClaim | None:
        return await self._claim_delivery(now=now, lease_seconds=lease_seconds)

    async def claim_delivery_by_id(
        self,
        outbox_id: int,
        *,
        now: datetime,
        lease_seconds: int = 300,
    ) -> DeliveryClaim | None:
        return await self._claim_delivery(
            now=now,
            lease_seconds=lease_seconds,
            outbox_id=outbox_id,
        )

    async def _claim_delivery(
        self,
        *,
        now: datetime,
        lease_seconds: int,
        outbox_id: int | None = None,
    ) -> DeliveryClaim | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        lease_token = uuid4().hex
        due = or_(
            DeliveryOutbox.status == "pending",
            (DeliveryOutbox.status == "retry_wait")
            & (DeliveryOutbox.next_attempt_at <= now),
            (DeliveryOutbox.status == "sending")
            & (DeliveryOutbox.lease_expires_at <= now)
            & (DeliveryOutbox.attempt_count < 4),
        )
        next_delivery = (
            select(DeliveryOutbox.id)
            .where(due)
            .where(DeliveryOutbox.id == outbox_id if outbox_id is not None else literal(True))
            .order_by(DeliveryOutbox.created_at, DeliveryOutbox.id)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(DeliveryOutbox)
            .where(DeliveryOutbox.id == next_delivery)
            .values(
                status="sending",
                attempt_count=DeliveryOutbox.attempt_count + 1,
                next_attempt_at=None,
                lease_token=lease_token,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                safe_error_code=None,
            )
            .returning(DeliveryOutbox)
        )
        async with self._session_factory.begin() as session:
            await session.execute(
                update(DeliveryOutbox)
                .where(
                    DeliveryOutbox.status == "sending",
                    DeliveryOutbox.lease_expires_at <= now,
                    DeliveryOutbox.attempt_count >= 4,
                )
                .values(
                    status="failed",
                    next_attempt_at=None,
                    lease_token=None,
                    lease_expires_at=None,
                    safe_error_code="lease_expired",
                )
            )
            row = (await session.execute(statement)).scalar_one_or_none()
            return None if row is None else _delivery_claim(row)

    async def mark_delivery_sent(
        self,
        claim: DeliveryClaim,
        *,
        message_id: str,
        now: datetime,
    ) -> None:
        async with self._session_factory.begin() as session:
            updated_id = (
                await session.execute(
                    update(DeliveryOutbox)
                    .where(
                        DeliveryOutbox.id == claim.id,
                        DeliveryOutbox.status == "sending",
                        DeliveryOutbox.lease_token == claim.lease_token,
                    )
                    .values(
                        status="sent",
                        next_attempt_at=None,
                        lease_token=None,
                        lease_expires_at=None,
                        safe_error_code=None,
                        feishu_message_id=message_id,
                        sent_at=now,
                    )
                    .returning(DeliveryOutbox.id)
                )
            ).scalar_one_or_none()
            if updated_id is None:
                raise StaleLeaseError("delivery lease is no longer current")
            if claim.kind is MessageKind.DAILY_REPORT:
                report_date = _report_date_from_key(claim.idempotency_key)
                report_id = (
                    await session.execute(
                        update(DailyReport)
                        .where(
                            DailyReport.group_id == claim.group_id,
                            DailyReport.report_date == report_date,
                            DailyReport.status == "queued",
                        )
                        .values(status="sent", sent_at=now)
                        .returning(DailyReport.id)
                    )
                ).scalar_one_or_none()
                if report_id is None:
                    raise RuntimeError("linked daily report is not queued")

    async def fail_delivery(
        self, claim: DeliveryClaim, code: str, *, now: datetime
    ) -> None:
        retry_index = claim.attempt_count - 1
        if retry_index < len(RETRY_DELAYS):
            status = "retry_wait"
            next_attempt_at = now + RETRY_DELAYS[retry_index]
        else:
            status = "failed"
            next_attempt_at = None
        async with self._session_factory.begin() as session:
            updated_id = (
                await session.execute(
                    update(DeliveryOutbox)
                    .where(
                        DeliveryOutbox.id == claim.id,
                        DeliveryOutbox.status == "sending",
                        DeliveryOutbox.lease_token == claim.lease_token,
                    )
                    .values(
                        status=status,
                        next_attempt_at=next_attempt_at,
                        lease_token=None,
                        lease_expires_at=None,
                        safe_error_code=code,
                    )
                    .returning(DeliveryOutbox.id)
                )
            ).scalar_one_or_none()
            if updated_id is None:
                raise StaleLeaseError("delivery lease is no longer current")

    async def skip_delivery(self, claim: DeliveryClaim, code: str) -> None:
        async with self._session_factory.begin() as session:
            updated_id = (
                await session.execute(
                    update(DeliveryOutbox)
                    .where(
                        DeliveryOutbox.id == claim.id,
                        DeliveryOutbox.status == "sending",
                        DeliveryOutbox.lease_token == claim.lease_token,
                    )
                    .values(
                        status="skipped",
                        next_attempt_at=None,
                        lease_token=None,
                        lease_expires_at=None,
                        safe_error_code=code,
                    )
                    .returning(DeliveryOutbox.id)
                )
            ).scalar_one_or_none()
            if updated_id is None:
                raise StaleLeaseError("delivery lease is no longer current")

    async def list_outbox(self, ids: tuple[int, ...]) -> tuple[DeliveryOutbox, ...]:
        if not ids:
            return ()
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(DeliveryOutbox)
                .where(DeliveryOutbox.id.in_(ids))
                .order_by(DeliveryOutbox.id)
            )
            return tuple(rows.all())

    async def next_delivery_time(self, outbox_id: int) -> datetime | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(DeliveryOutbox.next_attempt_at).where(
                    DeliveryOutbox.id == outbox_id
                )
            )

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


def _payload_items(payload: dict[str, object]) -> list[dict[str, object]]:
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _alert_item_allowed(
    item: dict[str, object], recent_items: list[dict[str, object]]
) -> bool:
    fingerprint = item.get("event_fingerprint")
    if not isinstance(fingerprint, str):
        return True
    matching = [recent for recent in recent_items if recent.get("event_fingerprint") == fingerprint]
    if not matching:
        return True
    risk = item.get("risk_level")
    risk_rank = _RISK_ORDER.get(risk, -1) if isinstance(risk, str) else -1
    previous_risk = max(
        (
            _RISK_ORDER.get(previous, -1)
            for previous in (recent.get("risk_level") for recent in matching)
            if isinstance(previous, str)
        ),
        default=-1,
    )
    if risk_rank > previous_risk:
        return True
    version_id = item.get("document_version_id")
    content_hash = item.get("content_hash")
    return all(
        version_id != recent.get("document_version_id")
        and content_hash != recent.get("content_hash")
        for recent in matching
    )


def _with_alert_items(
    message: DeliveryMessage, items: list[dict[str, object]]
) -> DeliveryMessage:
    original_items = _payload_items(message.payload)
    if items == original_items:
        return message
    payload = dict(message.payload)
    payload["items"] = items
    if message.kind is MessageKind.MEDIUM_ALERT_BATCH:
        payload["title"] = (
            "早期信号·待核实"
            if any(item.get("verification_status") == "early_signal" for item in items)
            else "中风险预警汇总"
        )
    identities = "|".join(
        sorted(
            f"{item.get('event_fingerprint')}:{item.get('risk_level')}:"
            f"{item.get('document_version_id')}:{item.get('content_hash')}"
            for item in items
        )
    )
    digest = hashlib.sha256(identities.encode("utf-8")).hexdigest()
    bucket = message.idempotency_key.rsplit(":", 1)[-1]
    return DeliveryMessage(
        idempotency_key=f"alert-filtered:{message.group_id}:{digest}:{bucket}",
        group_id=message.group_id,
        kind=message.kind,
        payload=payload,
        reply_to_message_id=message.reply_to_message_id,
        reply_in_thread=message.reply_in_thread,
    )


def _delivery_claim(row: DeliveryOutbox) -> DeliveryClaim:
    if row.lease_token is None:
        raise StaleLeaseError("claimed delivery has no lease token")
    return DeliveryClaim(
        id=row.id,
        idempotency_key=row.idempotency_key,
        group_id=row.group_id,
        kind=MessageKind(row.message_kind),
        payload=row.payload,
        reply_to_message_id=row.reply_to_message_id,
        reply_in_thread=row.reply_in_thread,
        attempt_count=row.attempt_count,
        lease_token=row.lease_token,
    )


def _report_date_from_key(idempotency_key: str) -> date:
    try:
        prefix, _, raw_date = idempotency_key.partition(":")
        if prefix != "daily":
            raise ValueError
        return date.fromisoformat(raw_date.rsplit(":", 1)[-1])
    except ValueError as error:
        raise ValueError("invalid daily report idempotency key") from error


def _scored_analysis(
    analysis: DocumentAnalysis,
    job: AnalysisJob,
    version: DocumentVersion,
    document: Document,
    source: Source,
    *,
    platforms: tuple[Platform, ...],
) -> ScoredAnalysis:
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
        platforms=platforms,
        regions=tuple(source.regions),
    )
    risk = RiskLevel(analysis.risk_level)
    return ScoredAnalysis(
        analysis_id=analysis.id,
        candidate=candidate,
        result=AnalysisResult.model_validate(analysis.structured_payload),
        evidence_confidence=analysis.evidence_confidence,
        resolution=RiskResolution(risk_level=risk, rule_hits=(), needs_review=False),
        event_fingerprint=analysis.event_fingerprint,
    )
