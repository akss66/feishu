from __future__ import annotations

from datetime import date
from pathlib import Path
from xml.etree import ElementTree

from sqlalchemy import func, select

from commerce_agent.ingestion.collectors import ApiCollector, FeedCollector
from commerce_agent.ingestion.compliance import CompliancePolicy
from commerce_agent.ingestion.extract import ContentExtractor, LanguageDetection
from commerce_agent.ingestion.http import FetchRequest, FetchResponse
from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    Platform,
    RunStatus,
    SourceDefinition,
    Trigger,
    TrustTier,
)
from commerce_agent.ingestion.registry import SourceRegistry
from commerce_agent.ingestion.service import IngestionService
from commerce_agent.ingestion.snapshots import SnapshotStore
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.ingestion import SqlAlchemyIngestionRepository
from commerce_agent.persistence.models import Document, DocumentVersion, FetchRun, SourceHealth

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ingestion"


class StaticLanguageDetector:
    def detect(self, text: str) -> LanguageDetection:
        del text
        return LanguageDetection("en", 1.0)


class FixtureHttp:
    def __init__(self, responses: dict[str, FetchResponse]) -> None:
        self.responses = responses
        self.requests: list[FetchRequest] = []

    async def get(self, request: FetchRequest) -> FetchResponse:
        self.requests.append(request)
        return self.responses[request.url]


def source(source_id: str, kind: CollectorKind) -> SourceDefinition:
    host = "feeds.example.com" if kind is CollectorKind.RSS else "api.example.com"
    config: dict[str, str | int]
    if kind is CollectorKind.RSS:
        config = {"item_limit": 10}
    else:
        config = {
            "items_path": "data.items",
            "url_field": "url",
            "title_field": "headline",
            "published_at_field": "published_at",
            "item_limit": 10,
        }
    return SourceDefinition(
        source_id=source_id,
        name=f"Fixture {source_id}",
        entry_url=f"https://{host}/updates",
        platforms=(Platform.AMAZON,),
        trust_tier=TrustTier.OFFICIAL,
        collector=kind,
        compliance=ComplianceStatus.ALLOWED,
        enabled=True,
        regions=("global",),
        language_hint="en",
        interval_minutes=120,
        terms_url=f"https://{host}/terms",
        robots_url=f"https://{host}/robots.txt",
        reviewed_at=date(2026, 7, 20),
        compliance_notes="Fixture-only approved public source.",
        collector_config=config,
    )


def rss_body() -> bytes:
    wrapper = ElementTree.fromstring((FIXTURES / "feed.xml").read_bytes())
    rss_text = next(document.text for document in wrapper if document.attrib["name"] == "rss")
    rss = ElementTree.fromstring(rss_text)
    channel = rss.find("channel")
    assert channel is not None
    entries = channel.findall("item")
    for entry in entries[2:]:
        channel.remove(entry)
    shared_body = (FIXTURES / "article_en.html").read_text(encoding="utf-8")
    first_description = entries[0].find("description")
    assert first_description is not None
    first_description.text = shared_body
    duplicate_link = entries[1].find("link")
    assert duplicate_link is not None
    duplicate_link.text = "/news/fee-update-copy"
    duplicate_description = ElementTree.SubElement(entries[1], "description")
    duplicate_description.text = shared_body
    return ElementTree.tostring(rss, encoding="utf-8", xml_declaration=True)


async def build_pipeline(tmp_path: Path):
    feed_source = source("fixture-feed", CollectorKind.RSS)
    api_source = source("fixture-api", CollectorKind.API)
    feed_body = rss_body()
    api_body = (FIXTURES / "api.json").read_bytes()
    http = FixtureHttp(
        {
            feed_source.entry_url: FetchResponse(
                url=feed_source.entry_url,
                status_code=200,
                headers={"content-type": "application/rss+xml", "etag": '"feed-v1"'},
                body=feed_body,
            ),
            api_source.entry_url: FetchResponse(
                url=api_source.entry_url,
                status_code=200,
                headers={"content-type": "application/json", "etag": '"api-v1"'},
                body=api_body,
            ),
        }
    )
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pipeline.db'}")
    await database.create_schema()
    service = IngestionService(
        registry=SourceRegistry([feed_source, api_source]),
        compliance=CompliancePolicy(),
        collectors={
            CollectorKind.RSS: FeedCollector(http),
            CollectorKind.API: ApiCollector(http),
        },
        extractor=ContentExtractor(StaticLanguageDetector()),
        snapshot_store=SnapshotStore(tmp_path / "snapshots"),
        repository=SqlAlchemyIngestionRepository(database.session),
        max_concurrency=2,
    )
    return service, database, http, feed_source, api_source, feed_body


async def test_full_pipeline_is_idempotent_versions_changes_groups_and_handles_304(
    tmp_path: Path,
) -> None:
    service, database, http, feed_source, _, original_feed = await build_pipeline(tmp_path)
    try:
        first = await service.run_all(Trigger.SCHEDULED)

        assert all(summary.status is RunStatus.SUCCESS for summary in first)
        async with database.session() as session:
            first_version_count = await session.scalar(
                select(func.count()).select_from(DocumentVersion)
            )
            feed_documents = (
                await session.scalars(
                    select(Document)
                    .where(Document.source_id == feed_source.source_id)
                    .order_by(Document.canonical_url)
                )
            ).all()
        assert first_version_count == 5
        assert len(feed_documents) == 2
        assert feed_documents[0].canonical_url != feed_documents[1].canonical_url
        assert feed_documents[0].content_group_hash == feed_documents[1].content_group_hash

        second = await service.run_all(Trigger.SCHEDULED)

        assert all(summary.status is RunStatus.SUCCESS for summary in second)
        assert sum(summary.skipped for summary in second) == 5
        async with database.session() as session:
            identical_version_count = await session.scalar(
                select(func.count()).select_from(DocumentVersion)
            )
        assert identical_version_count == first_version_count

        changed_feed = original_feed.replace(
            b"updated marketplace fee policy", b"revised marketplace fee policy", 1
        )
        http.responses[feed_source.entry_url] = FetchResponse(
            url=feed_source.entry_url,
            status_code=200,
            headers={"content-type": "application/rss+xml", "etag": '"feed-v2"'},
            body=changed_feed,
        )
        changed = await service.run_source(feed_source.source_id, Trigger.MANUAL)

        assert changed.status is RunStatus.SUCCESS
        assert (changed.updated, changed.skipped) == (1, 1)
        async with database.session() as session:
            changed_version_count = await session.scalar(
                select(func.count()).select_from(DocumentVersion)
            )
        assert changed_version_count == first_version_count + 1

        http.responses[feed_source.entry_url] = FetchResponse(
            url=feed_source.entry_url,
            status_code=304,
            headers={"etag": '"feed-v2"'},
        )
        not_modified = await service.run_source(feed_source.source_id, Trigger.SCHEDULED)

        assert not_modified.status is RunStatus.SUCCESS
        assert not_modified.discovered == 0
        assert http.requests[-1].etag == '"feed-v2"'
        async with database.session() as session:
            final_version_count = await session.scalar(
                select(func.count()).select_from(DocumentVersion)
            )
            health = await session.get(SourceHealth, feed_source.source_id)
            latest_run = await session.scalar(
                select(FetchRun)
                .where(FetchRun.source_id == feed_source.source_id)
                .order_by(FetchRun.id.desc())
            )
        assert final_version_count == changed_version_count
        assert health is not None and health.health_status == "healthy"
        assert health.last_success_at is not None
        assert latest_run is not None and latest_run.status == RunStatus.SUCCESS.value
        assert len(list((tmp_path / "snapshots").rglob("*.bin.gz"))) == 5
    finally:
        await database.dispose()


async def test_run_all_does_not_cancel_a_healthy_source_when_another_returns_500(
    tmp_path: Path,
) -> None:
    service, database, http, _, api_source, _ = await build_pipeline(tmp_path)
    http.responses[api_source.entry_url] = FetchResponse(
        url=api_source.entry_url,
        status_code=500,
        headers={"content-type": "application/json"},
        body=b'{"error":"fixture failure"}',
    )
    try:
        summaries = await service.run_all(Trigger.SCHEDULED)

        by_source = {summary.source_id: summary for summary in summaries}
        assert by_source["fixture-api"].status is RunStatus.FAILED
        assert by_source["fixture-api"].error_code == "fetch_failed"
        assert by_source["fixture-feed"].status is RunStatus.SUCCESS
        assert by_source["fixture-feed"].created == 2
        async with database.session() as session:
            document_count = await session.scalar(select(func.count()).select_from(Document))
            run_count = await session.scalar(select(func.count()).select_from(FetchRun))
        assert document_count == 2
        assert run_count == 2
    finally:
        await database.dispose()
