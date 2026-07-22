"""Idempotent orchestration for approved public source ingestion."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from commerce_agent.ingestion.collectors import Collector, CollectorError
from commerce_agent.ingestion.compliance import CompliancePolicy, CompliancePolicyError
from commerce_agent.ingestion.dedupe import fingerprint_document
from commerce_agent.ingestion.extract import ContentExtractor, ExtractionError
from commerce_agent.ingestion.http import FetchError, FetchResponse
from commerce_agent.ingestion.models import (
    CollectedFailure,
    CollectedItem,
    CollectorKind,
    FetchContext,
    FetchMetrics,
    ResponseArtifact,
    RunStatus,
    RunSummary,
    SourceDefinition,
    Trigger,
)
from commerce_agent.ingestion.registry import SourceRegistry
from commerce_agent.ingestion.snapshots import SnapshotStore, SnapshotStoreError
from commerce_agent.persistence.ingestion import (
    IngestionRepository,
    PersistableDocument,
    PersistOutcome,
)

_LOGGER = logging.getLogger(__name__)
_KNOWN_ERROR_CODES = frozenset(
    {
        "blank_content",
        "collector_unavailable",
        "compliance_not_allowed",
        "compliance_review_required",
        "destination_not_public",
        "detail_fetch_failed",
        "dns_resolution_failed",
        "fetch_failed",
        "hash_path_conflict",
        "host_not_allowed",
        "http_client_error",
        "http_transport_error",
        "invalid_config",
        "invalid_payload",
        "invalid_selector",
        "invalid_source_id",
        "invalid_url",
        "item_limit_exceeded",
        "network_retry_exhausted",
        "path_outside_root",
        "port_not_allowed",
        "redirect_missing_location",
        "redirect_status_not_supported",
        "renderer_failed",
        "renderer_response_unavailable",
        "renderer_security_rejected",
        "renderer_timeout",
        "renderer_unavailable",
        "response_artifact_missing",
        "response_too_large",
        "retry_exhausted",
        "scheme_not_allowed",
        "source_disabled",
        "source_already_running",
        "source_circuit_open",
        "too_many_redirects",
        "unexpected_http_status",
        "userinfo_not_allowed",
    }
)
_CONTROLLED_ERROR_CODES = _KNOWN_ERROR_CODES | frozenset(
    {
        "cancelled",
        "collector_error",
        "compliance_error",
        "extraction_error",
        "fetch_error",
        "snapshot_error",
        "unexpected_error",
    }
)


@dataclass(slots=True)
class _RunCounts:
    discovered: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    error_code: str | None = None

    @property
    def succeeded(self) -> int:
        return self.created + self.updated + self.skipped

    def record_failure(self, error: BaseException) -> None:
        self.failed += 1
        if self.error_code is None:
            self.error_code = _error_code(error)

    def record_error_code(self, error_code: str) -> None:
        self.failed += 1
        if self.error_code is None:
            self.error_code = _controlled_detail_error_code(error_code)

    def record_outcome(self, outcome: PersistOutcome) -> None:
        if outcome.created_document and outcome.created_version:
            self.created += 1
        elif outcome.created_version:
            self.updated += 1
        else:
            self.skipped += 1


class IngestionService:
    """Run collectors without overlapping a source or coupling source failures."""

    def __init__(
        self,
        *,
        registry: SourceRegistry,
        compliance: CompliancePolicy,
        collectors: Mapping[CollectorKind, Collector],
        extractor: ContentExtractor,
        snapshot_store: SnapshotStore,
        repository: IngestionRepository,
        max_concurrency: int = 4,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self._registry = registry
        self._compliance = compliance
        self._collectors = dict(collectors)
        self._extractor = extractor
        self._snapshot_store = snapshot_store
        self._repository = repository
        self._max_concurrency = max_concurrency
        self._clock = clock
        self._sync_lock = asyncio.Lock()
        self._sources_synced = False
        self._conditionals: dict[str, tuple[str | None, str | None]] = {}

    async def initialize(self) -> None:
        """Synchronize configured sources once before scheduled or manual work."""

        await self._ensure_sources_synced()

    async def run_source(
        self,
        source_id: str,
        trigger: Trigger = Trigger.MANUAL,
    ) -> RunSummary:
        source = self._registry.require(source_id)
        await self._ensure_sources_synced()
        started_at = self._clock()
        if (
            trigger is Trigger.SCHEDULED
            and await self._repository.is_source_suspended(source_id)
        ):
            return self._summary(
                source,
                trigger,
                started_at,
                RunStatus.SKIPPED,
                _RunCounts(error_code="source_circuit_open"),
                FetchMetrics(),
            )
        lease_token = await self._repository.claim_source(
            source_id,
            acquired_at=started_at,
        )
        if lease_token is None:
            return self._summary(
                source,
                trigger,
                started_at,
                RunStatus.SKIPPED,
                _RunCounts(error_code="source_already_running"),
                FetchMetrics(),
            )

        try:
            run_id = await self._repository.start_run(
                source_id,
                trigger,
                started_at=started_at,
            )
            metrics = FetchMetrics()
            try:
                summary = await self._run_started(source, trigger, started_at, metrics)
            except asyncio.CancelledError:
                summary = self._summary(
                    source,
                    trigger,
                    started_at,
                    RunStatus.FAILED,
                    _RunCounts(failed=1, error_code="cancelled"),
                    metrics,
                )
                await self._finish(run_id, summary)
                raise
            except Exception as error:
                summary = self._summary(
                    source,
                    trigger,
                    started_at,
                    RunStatus.FAILED,
                    _RunCounts(failed=1, error_code=_error_code(error)),
                    metrics,
                )
            await self._finish(run_id, summary)
            return summary
        finally:
            await self._repository.release_source(source_id, lease_token)

    async def run_all(
        self,
        trigger: Trigger = Trigger.SCHEDULED,
    ) -> tuple[RunSummary, ...]:
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def run_bounded(source: SourceDefinition) -> RunSummary:
            async with semaphore:
                return await self.run_source(source.source_id, trigger)

        results = await asyncio.gather(
            *(run_bounded(source) for source in self._registry.sources),
            return_exceptions=True,
        )
        summaries: list[RunSummary] = []
        for result in results:
            if isinstance(result, BaseException):
                raise result
            summaries.append(result)
        return tuple(summaries)

    async def _ensure_sources_synced(self) -> None:
        if self._sources_synced:
            return
        async with self._sync_lock:
            if self._sources_synced:
                return
            await self._repository.sync_sources(self._registry.sources)
            self._sources_synced = True

    async def _run_started(
        self,
        source: SourceDefinition,
        trigger: Trigger,
        started_at: datetime,
        metrics: FetchMetrics,
    ) -> RunSummary:
        counts = _RunCounts()
        try:
            self._compliance.require_collectable(source)
        except CompliancePolicyError as error:
            counts.error_code = _error_code(error)
            return self._summary(
                source,
                trigger,
                started_at,
                RunStatus.SKIPPED,
                counts,
                metrics,
            )

        collector = self._collectors.get(source.collector)
        if collector is None:
            counts.record_failure(CollectorError("collector_unavailable"))
            return self._summary(
                source,
                trigger,
                started_at,
                RunStatus.FAILED,
                counts,
                metrics,
            )

        etag, last_modified = self._conditionals.get(source.source_id, (None, None))
        context = FetchContext(
            trigger=trigger,
            started_at=started_at,
            etag=etag,
            last_modified=last_modified,
            metrics=metrics,
        )
        try:
            async for item in collector.collect(source, context):
                counts.discovered += 1
                if isinstance(item, CollectedFailure):
                    counts.record_error_code(item.error_code)
                    continue
                self._remember_conditionals(source.source_id, item)
                try:
                    outcome = await self._ingest_item(source, item)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    counts.record_failure(error)
                else:
                    counts.record_outcome(outcome)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            counts.record_failure(error)

        if counts.failed == 0:
            status = RunStatus.SUCCESS
        elif counts.succeeded:
            status = RunStatus.PARTIAL
        else:
            status = RunStatus.FAILED
        return self._summary(source, trigger, started_at, status, counts, metrics)

    async def _ingest_item(
        self,
        source: SourceDefinition,
        item: CollectedItem,
    ) -> PersistOutcome:
        if item.artifact is None:
            raise CollectorError("response_artifact_missing")
        snapshot = await self._snapshot_store.save(
            source.source_id,
            _artifact_response(item.artifact),
        )
        fetched_at = self._clock()
        document = self._extractor.extract(source, item, fetched_at=fetched_at)
        fingerprint = fingerprint_document(document.canonical_url, document.body)
        return await self._repository.persist_version(
            PersistableDocument(
                source_id=document.source_id,
                canonical_url=fingerprint.canonical_url,
                title=document.title,
                body=document.body,
                language=document.language,
                language_confidence=document.language_confidence,
                content_hash=fingerprint.content_hash,
                content_group_hash=fingerprint.content_group_hash,
                fetched_at=document.fetched_at,
                author=document.author,
                published_at=document.published_at,
                snapshot_path=snapshot.relative_path,
                etag=item.etag,
                last_modified=item.last_modified,
                publisher_key=_metadata_string(document.metadata, "publisher_key"),
                attribution=_metadata_string(document.metadata, "attribution"),
                content_scope=_metadata_string(document.metadata, "content_scope"),
            )
        )

    def _remember_conditionals(self, source_id: str, item: CollectedItem) -> None:
        previous_etag, previous_modified = self._conditionals.get(source_id, (None, None))
        self._conditionals[source_id] = (
            _safe_conditional(item.etag) or previous_etag,
            _safe_conditional(item.last_modified) or previous_modified,
        )

    def _summary(
        self,
        source: SourceDefinition,
        trigger: Trigger,
        started_at: datetime,
        status: RunStatus,
        counts: _RunCounts,
        metrics: FetchMetrics,
    ) -> RunSummary:
        return RunSummary(
            source_id=source.source_id,
            trigger=trigger,
            status=status,
            started_at=started_at,
            finished_at=self._clock(),
            discovered=counts.discovered,
            created=counts.created,
            updated=counts.updated,
            skipped=counts.skipped,
            failed=counts.failed,
            error_code=counts.error_code,
            http_requests=metrics.http_requests,
            http_not_modified=metrics.http_not_modified,
            bytes_received=metrics.bytes_received,
            error_summary=_controlled_error_summary(counts.error_code),
        )

    async def _finish(self, run_id: int, summary: RunSummary) -> None:
        await self._repository.finish_run(run_id, summary)
        self._log(summary)

    def _log(self, summary: RunSummary) -> None:
        _LOGGER.info(
            "ingestion_run_finished",
            extra={
                "source_id": summary.source_id,
                "category": summary.error_code or summary.status.value,
                "count_discovered": summary.discovered,
                "count_created": summary.created,
                "count_updated": summary.updated,
                "count_skipped": summary.skipped,
                "count_failed": summary.failed,
                "count_http_requests": summary.http_requests,
                "count_http_not_modified": summary.http_not_modified,
                "count_bytes_received": summary.bytes_received,
            },
        )


def _artifact_response(artifact: ResponseArtifact) -> FetchResponse:
    return FetchResponse(
        url=artifact.url,
        status_code=artifact.status_code,
        headers=artifact.headers,
        body=artifact.body,
    )


def _safe_conditional(value: str | None) -> str | None:
    if value is None or "\r" in value or "\n" in value:
        return None
    return value


def _metadata_string(metadata: Mapping[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code in _KNOWN_ERROR_CODES:
        return code
    if isinstance(error, CompliancePolicyError):
        return "compliance_error"
    if isinstance(error, CollectorError):
        return "collector_error"
    if isinstance(error, ExtractionError):
        return "extraction_error"
    if isinstance(error, SnapshotStoreError):
        return "snapshot_error"
    if isinstance(error, FetchError):
        return "fetch_error"
    return "unexpected_error"


def _controlled_error_summary(error_code: str | None) -> str | None:
    if error_code is None:
        return None
    if error_code in _CONTROLLED_ERROR_CODES:
        return error_code
    return "unexpected_error"


def _controlled_detail_error_code(error_code: str) -> str:
    if error_code in _KNOWN_ERROR_CODES:
        return error_code
    return "detail_fetch_failed"
