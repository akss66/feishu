from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

from commerce_agent.ingestion.collectors import CollectorError
from commerce_agent.ingestion.compliance import CompliancePolicy
from commerce_agent.ingestion.extract import ExtractionError
from commerce_agent.ingestion.models import (
    CollectedItem,
    CollectorKind,
    ComplianceStatus,
    ExtractedDocument,
    FetchContext,
    Platform,
    ResponseArtifact,
    RunStatus,
    RunSummary,
    SourceDefinition,
    Trigger,
    TrustTier,
)
from commerce_agent.ingestion.registry import SourceRegistry
from commerce_agent.ingestion.service import IngestionService
from commerce_agent.ingestion.snapshots import SnapshotRef
from commerce_agent.persistence.ingestion import PersistableDocument, PersistOutcome

NOW = datetime(2026, 7, 20, 10, tzinfo=UTC)


def source(
    source_id: str = "amazon-news",
    *,
    collector: CollectorKind = CollectorKind.RSS,
    enabled: bool = True,
    compliance: ComplianceStatus = ComplianceStatus.ALLOWED,
) -> SourceDefinition:
    return SourceDefinition(
        source_id=source_id,
        name=f"Source {source_id}",
        entry_url=f"https://{source_id}.example.com/updates",
        platforms=(Platform.AMAZON,),
        trust_tier=TrustTier.OFFICIAL,
        collector=collector,
        compliance=compliance,
        enabled=enabled,
        regions=("global",),
        language_hint="en",
        interval_minutes=120,
        terms_url=f"https://{source_id}.example.com/terms",
        robots_url=f"https://{source_id}.example.com/robots.txt",
        reviewed_at=date(2026, 7, 20),
        compliance_notes="Approved public source.",
        collector_config={},
    )


def item(
    body: bytes = b"good",
    *,
    suffix: str = "one",
    raw_body: bytes | None = None,
    with_artifact: bool = True,
) -> CollectedItem:
    url = f"https://amazon-news.example.com/{suffix}?token=never-log-me"
    return CollectedItem(
        url=url,
        body=body,
        content_type="text/plain; charset=utf-8",
        etag='"etag-v1"',
        last_modified="Mon, 20 Jul 2026 08:00:00 GMT",
        artifact=(
            ResponseArtifact(
                url=url,
                status_code=200,
                headers={
                    "content-type": "text/plain; charset=utf-8",
                    "etag": '"etag-v1"',
                    "last-modified": "Mon, 20 Jul 2026 08:00:00 GMT",
                },
                body=raw_body if raw_body is not None else b"raw:" + body,
            )
            if with_artifact
            else None
        ),
    )


class FakeCollector:
    def __init__(
        self,
        items: Sequence[CollectedItem] = (),
        *,
        events: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.items = tuple(items)
        self.events = events
        self.error = error
        self.calls: list[tuple[str, FetchContext]] = []

    async def collect(self, definition: SourceDefinition, context: FetchContext):
        self.calls.append((definition.source_id, context))
        if self.events is not None:
            self.events.append("collect")
        if self.error is not None:
            raise self.error
        for candidate in self.items:
            yield candidate


class FakeExtractor:
    def extract(
        self,
        definition: SourceDefinition,
        candidate: CollectedItem,
        *,
        fetched_at: datetime,
    ) -> ExtractedDocument:
        if candidate.body == b"bad":
            raise ExtractionError("blank_content")
        return ExtractedDocument(
            source_id=definition.source_id,
            canonical_url=candidate.url,
            title="Candidate",
            body=candidate.body.decode("utf-8"),
            language="en",
            language_confidence=1.0,
            fetched_at=fetched_at,
        )


class FakeSnapshotStore:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.saved: list[tuple[str, bytes]] = []

    async def save(self, source_id: str, response) -> SnapshotRef:
        if self.events is not None:
            self.events.append("snapshot")
        self.saved.append((source_id, response.body))
        return SnapshotRef(
            relative_path=f"2026/07/20/{source_id}/candidate.bin.gz",
            sha256="a" * 64,
            media_type="text/plain",
            byte_count=len(response.body),
        )


class FakeRepository:
    def __init__(
        self,
        outcomes: Sequence[PersistOutcome] = (),
        *,
        events: list[str] | None = None,
        persist_error: Exception | None = None,
    ) -> None:
        self._outcomes = list(outcomes)
        self.events = events
        self.persist_error = persist_error
        self.synced: list[tuple[SourceDefinition, ...]] = []
        self.started: list[tuple[int, str, Trigger, datetime | None]] = []
        self.persisted: list[PersistableDocument] = []
        self.finished: list[tuple[int, RunSummary]] = []

    async def sync_sources(self, sources: Sequence[SourceDefinition]) -> None:
        self.synced.append(tuple(sources))

    async def start_run(
        self,
        source_id: str,
        trigger: Trigger,
        *,
        started_at: datetime | None = None,
    ) -> int:
        run_id = len(self.started) + 1
        self.started.append((run_id, source_id, trigger, started_at))
        return run_id

    async def find_document(self, source_id: str, canonical_url: str):
        del source_id, canonical_url
        return None

    async def persist_version(self, candidate: PersistableDocument) -> PersistOutcome:
        if self.events is not None:
            self.events.append("persist")
        if self.persist_error is not None:
            raise self.persist_error
        self.persisted.append(candidate)
        if self._outcomes:
            return self._outcomes.pop(0)
        version_id = len(self.persisted)
        return PersistOutcome(
            document_id=version_id,
            version_id=version_id,
            created_document=True,
            created_version=True,
        )

    async def finish_run(self, run_id: int, summary: RunSummary) -> None:
        self.finished.append((run_id, summary))


class RecordingCompliance:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def require_collectable(self, definition: SourceDefinition) -> None:
        del definition
        self.events.append("compliance")


def service(
    definitions: Sequence[SourceDefinition],
    collectors: Mapping[CollectorKind, object],
    *,
    repository: FakeRepository | None = None,
    snapshot_store: FakeSnapshotStore | None = None,
    compliance: object | None = None,
    max_concurrency: int = 4,
    clock: Callable[[], datetime] = lambda: NOW,
) -> tuple[IngestionService, FakeRepository, FakeSnapshotStore]:
    stored_repository = repository or FakeRepository()
    stored_snapshots = snapshot_store or FakeSnapshotStore()
    return (
        IngestionService(
            registry=SourceRegistry(definitions),
            compliance=compliance or CompliancePolicy(),
            collectors=collectors,
            extractor=FakeExtractor(),
            snapshot_store=stored_snapshots,
            repository=stored_repository,
            max_concurrency=max_concurrency,
            clock=clock,
        ),
        stored_repository,
        stored_snapshots,
    )


@pytest.mark.parametrize("error_summary", ["x" * 513, "unsafe\nsummary"])
def test_run_summary_rejects_unsafe_error_summaries(error_summary: str) -> None:
    with pytest.raises(ValueError):
        RunSummary(
            source_id="amazon-news",
            trigger=Trigger.MANUAL,
            status=RunStatus.FAILED,
            started_at=NOW,
            finished_at=NOW,
            failed=1,
            error_code="unexpected_error",
            error_summary=error_summary,
        )


async def test_checks_compliance_before_invoking_the_collector() -> None:
    events: list[str] = []
    collector = FakeCollector(events=events)
    ingestion, _, _ = service(
        [source()],
        {CollectorKind.RSS: collector},
        compliance=RecordingCompliance(events),
    )

    summary = await ingestion.run_source("amazon-news", Trigger.MANUAL)

    assert events == ["compliance", "collect"]
    assert summary.status is RunStatus.SUCCESS


async def test_persists_the_same_started_at_exposed_by_the_run_summary() -> None:
    ingestion, repository, _ = service(
        [source()],
        {CollectorKind.RSS: FakeCollector()},
    )

    summary = await ingestion.run_source("amazon-news", Trigger.MANUAL)

    assert repository.started == [(1, "amazon-news", Trigger.MANUAL, summary.started_at)]


async def test_disabled_source_is_finished_as_skipped_without_collection() -> None:
    collector = FakeCollector([item()])
    ingestion, repository, _ = service(
        [source(enabled=False)],
        {CollectorKind.RSS: collector},
    )

    summary = await ingestion.run_source("amazon-news", Trigger.SCHEDULED)

    assert summary.status is RunStatus.SKIPPED
    assert summary.error_code == "source_disabled"
    assert collector.calls == []
    assert repository.finished == [(1, summary)]


async def test_nonallowed_source_is_finished_as_skipped_without_collection() -> None:
    collector = FakeCollector([item()])
    ingestion, repository, _ = service(
        [source(compliance=ComplianceStatus.PENDING_REVIEW)],
        {CollectorKind.RSS: collector},
    )

    summary = await ingestion.run_source("amazon-news", Trigger.MANUAL)

    assert summary.status is RunStatus.SKIPPED
    assert summary.error_code == "compliance_not_allowed"
    assert collector.calls == []
    assert repository.finished == [(1, summary)]


async def test_unexpected_compliance_failure_still_finishes_the_started_run() -> None:
    class BrokenCompliance:
        def require_collectable(self, definition: SourceDefinition) -> None:
            del definition
            raise RuntimeError("policy backend leaked secret-value")

    collector = FakeCollector([item()])
    ingestion, repository, _ = service(
        [source()],
        {CollectorKind.RSS: collector},
        compliance=BrokenCompliance(),
    )

    summary = await ingestion.run_source("amazon-news", Trigger.MANUAL)

    assert summary.status is RunStatus.FAILED
    assert summary.error_code == "unexpected_error"
    assert collector.calls == []
    assert repository.finished == [(1, summary)]


async def test_collector_cancellation_finishes_failed_then_propagates() -> None:
    entered = asyncio.Event()
    never = asyncio.Event()

    class BlockingCollector:
        async def collect(self, definition: SourceDefinition, context: FetchContext):
            del definition
            context.metrics.record_response(status_code=200, bytes_received=17)
            entered.set()
            await never.wait()
            yield item()

    ingestion, repository, _ = service(
        [source()],
        {CollectorKind.RSS: BlockingCollector()},
    )
    task = asyncio.create_task(ingestion.run_source("amazon-news", Trigger.MANUAL))
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(repository.finished) == 1
    summary = repository.finished[0][1]
    assert summary.status is RunStatus.FAILED
    assert summary.error_code == "cancelled"
    assert (summary.http_requests, summary.http_not_modified, summary.bytes_received) == (
        1,
        0,
        17,
    )
    assert summary.error_summary == "cancelled"


async def test_item_ingestion_cancellation_finishes_failed_then_propagates() -> None:
    entered = asyncio.Event()
    never = asyncio.Event()

    class BlockingSnapshotStore(FakeSnapshotStore):
        async def save(self, source_id: str, response) -> SnapshotRef:
            del source_id, response
            entered.set()
            await never.wait()
            raise AssertionError("unreachable")

    repository = FakeRepository()
    ingestion, _, _ = service(
        [source()],
        {CollectorKind.RSS: FakeCollector([item()])},
        repository=repository,
        snapshot_store=BlockingSnapshotStore(),
    )
    task = asyncio.create_task(ingestion.run_source("amazon-news", Trigger.MANUAL))
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert repository.persisted == []
    assert len(repository.finished) == 1
    summary = repository.finished[0][1]
    assert summary.status is RunStatus.FAILED
    assert summary.error_code == "cancelled"


async def test_same_source_runs_never_overlap() -> None:
    release = asyncio.Event()

    @dataclass
    class LockingCollector:
        active: int = 0
        max_active: int = 0
        calls: int = 0

        async def collect(self, definition: SourceDefinition, context: FetchContext):
            del definition, context
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.calls == 1:
                await release.wait()
            yield item(suffix=str(self.calls))
            self.active -= 1

    collector = LockingCollector()
    ingestion, _, _ = service([source()], {CollectorKind.RSS: collector})

    first = asyncio.create_task(ingestion.run_source("amazon-news", Trigger.MANUAL))
    await asyncio.sleep(0)
    second = asyncio.create_task(ingestion.run_source("amazon-news", Trigger.SCHEDULED))
    await asyncio.sleep(0)

    assert collector.calls == 1
    release.set()
    summaries = await asyncio.gather(first, second)

    assert collector.max_active == 1
    assert [summary.status for summary in summaries] == [
        RunStatus.SUCCESS,
        RunStatus.SUCCESS,
    ]


@pytest.mark.parametrize("kind", list(CollectorKind))
async def test_routes_each_source_to_its_configured_collector(kind: CollectorKind) -> None:
    collectors = {candidate: FakeCollector() for candidate in CollectorKind}
    ingestion, _, _ = service([source(collector=kind)], collectors)

    await ingestion.run_source("amazon-news", Trigger.MANUAL)

    assert [candidate for candidate, collector in collectors.items() if collector.calls] == [kind]


async def test_saves_candidate_snapshot_before_persisting_a_version() -> None:
    events: list[str] = []
    repository = FakeRepository(events=events)
    snapshots = FakeSnapshotStore(events)
    ingestion, _, _ = service(
        [source()],
        {
            CollectorKind.RSS: FakeCollector(
                [item(b"rendered candidate", raw_body=b"raw HTTP response")]
            )
        },
        repository=repository,
        snapshot_store=snapshots,
    )

    summary = await ingestion.run_source("amazon-news", Trigger.MANUAL)

    assert events == ["snapshot", "persist"]
    assert snapshots.saved == [("amazon-news", b"raw HTTP response")]
    assert repository.persisted[0].snapshot_path.endswith("candidate.bin.gz")
    assert summary.created == 1


async def test_missing_response_artifact_fails_item_without_snapshot_or_persist() -> None:
    ingestion, repository, snapshots = service(
        [source()],
        {
            CollectorKind.RSS: FakeCollector(
                [item(b"candidate without raw response", with_artifact=False)]
            )
        },
    )

    summary = await ingestion.run_source("amazon-news", Trigger.MANUAL)

    assert summary.status is RunStatus.FAILED
    assert summary.error_code == "response_artifact_missing"
    assert snapshots.saved == []
    assert repository.persisted == []


async def test_item_extraction_failure_is_counted_while_other_items_continue() -> None:
    collector = FakeCollector([item(b"bad", suffix="bad"), item(b"good", suffix="good")])
    ingestion, repository, snapshots = service(
        [source()],
        {CollectorKind.RSS: collector},
    )

    summary = await ingestion.run_source("amazon-news", Trigger.MANUAL)

    assert summary.status is RunStatus.PARTIAL
    assert (summary.discovered, summary.created, summary.failed) == (2, 1, 1)
    assert summary.error_code == "blank_content"
    assert [candidate.body for candidate in repository.persisted] == ["good"]
    assert snapshots.saved == [
        ("amazon-news", b"raw:bad"),
        ("amazon-news", b"raw:good"),
    ]


async def test_run_summary_stably_classifies_create_update_and_duplicate() -> None:
    repository = FakeRepository(
        outcomes=[
            PersistOutcome(1, 1, created_document=True, created_version=True),
            PersistOutcome(1, 2, created_document=False, created_version=True),
            PersistOutcome(1, 2, created_document=False, created_version=False),
        ]
    )
    collector = FakeCollector(
        [item(suffix="new"), item(suffix="changed"), item(suffix="same")]
    )
    ingestion, _, _ = service(
        [source()],
        {CollectorKind.RSS: collector},
        repository=repository,
    )

    summary = await ingestion.run_source("amazon-news", Trigger.MANUAL)

    assert summary.status is RunStatus.SUCCESS
    assert (
        summary.discovered,
        summary.created,
        summary.updated,
        summary.skipped,
        summary.failed,
    ) == (3, 1, 1, 1, 0)
    assert summary.error_code is None
    assert repository.finished == [(1, summary)]


async def test_collector_failure_never_treats_a_safe_looking_secret_as_a_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "supersecretvalue"
    collector = FakeCollector(error=CollectorError(secret))
    ingestion, repository, _ = service(
        [source()],
        {CollectorKind.RSS: collector},
    )

    with caplog.at_level(logging.INFO, logger="commerce_agent.ingestion.service"):
        summary = await ingestion.run_source("amazon-news", Trigger.MANUAL)

    assert summary.status is RunStatus.FAILED
    assert summary.error_code == "collector_error"
    assert repository.finished == [(1, summary)]
    assert secret not in "\n".join(
        record.getMessage() + repr(record.__dict__) for record in caplog.records
    )


async def test_run_all_bounds_parallelism_and_isolates_source_failures() -> None:
    @dataclass
    class ConcurrentCollector:
        active: int = 0
        max_active: int = 0

        async def collect(self, definition: SourceDefinition, context: FetchContext):
            del context
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            if definition.source_id == "source-b":
                raise CollectorError("fetch_failed")
            yield item(suffix=definition.source_id)

    collector = ConcurrentCollector()
    definitions = [source(f"source-{letter}") for letter in "abcd"]
    ingestion, repository, _ = service(
        definitions,
        {CollectorKind.RSS: collector},
        max_concurrency=2,
    )

    summaries = await ingestion.run_all(Trigger.SCHEDULED)

    assert collector.max_active == 2
    assert [summary.source_id for summary in summaries] == [
        "source-a",
        "source-b",
        "source-c",
        "source-d",
    ]
    assert [summary.status for summary in summaries] == [
        RunStatus.SUCCESS,
        RunStatus.FAILED,
        RunStatus.SUCCESS,
        RunStatus.SUCCESS,
    ]
    assert len(repository.finished) == 4


async def test_run_all_surfaces_finish_storage_failure_after_other_sources_complete() -> None:
    class FinishFailingRepository(FakeRepository):
        async def finish_run(self, run_id: int, summary: RunSummary) -> None:
            if summary.source_id == "source-b":
                raise RuntimeError("finish storage unavailable")
            await super().finish_run(run_id, summary)

    repository = FinishFailingRepository()
    collector = FakeCollector([item()])
    ingestion, _, _ = service(
        [source("source-a"), source("source-b")],
        {CollectorKind.RSS: collector},
        repository=repository,
        max_concurrency=2,
    )

    with pytest.raises(RuntimeError, match="finish storage unavailable"):
        await ingestion.run_all(Trigger.SCHEDULED)

    assert len(repository.started) == 2
    assert [summary.source_id for _, summary in repository.finished] == ["source-a"]


async def test_logs_only_source_category_and_counts_not_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "super-secret-value"
    collector = FakeCollector(
        error=RuntimeError(f"request failed https://example.com/items?token={secret}")
    )
    ingestion, _, _ = service(
        [source()],
        {CollectorKind.RSS: collector},
    )

    with caplog.at_level(logging.INFO, logger="commerce_agent.ingestion.service"):
        summary = await ingestion.run_source("amazon-news", Trigger.MANUAL)

    rendered = "\n".join(record.getMessage() + repr(record.__dict__) for record in caplog.records)
    assert summary.error_code == "unexpected_error"
    assert summary.error_summary == "unexpected_error"
    assert secret not in rendered
    assert "?token=" not in rendered
    assert all(record.source_id == "amazon-news" for record in caplog.records)
    assert all(
        set(
            (
                "source_id",
                "category",
                "count_discovered",
                "count_created",
                "count_updated",
                "count_skipped",
                "count_failed",
            )
        )
        <= record.__dict__.keys()
        for record in caplog.records
    )
