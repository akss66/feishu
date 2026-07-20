from __future__ import annotations

import asyncio
import gzip
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

from sqlalchemy import func, select

from commerce_agent.ingestion.collectors import (
    ApiCollector,
    BrowserCollector,
    BrowserRequest,
    FeedCollector,
    HtmlCollector,
    RenderedPage,
    SitemapCollector,
)
from commerce_agent.ingestion.compliance import CompliancePolicy
from commerce_agent.ingestion.extract import ContentExtractor, LanguageDetection
from commerce_agent.ingestion.http import FetchRequest, FetchResponse
from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    Platform,
    ResponseArtifact,
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
        response = self.responses[request.url]
        if request.metrics is not None:
            request.metrics.record_request(
                status_code=response.status_code,
                bytes_received=len(response.body),
            )
        return response


class FixtureBrowser:
    def __init__(self, pages: dict[str, RenderedPage]) -> None:
        self.pages = pages
        self.requests: list[BrowserRequest] = []

    async def render(self, request: BrowserRequest) -> RenderedPage:
        self.requests.append(request)
        page = self.pages[request.url]
        request.metrics.record_request(
            status_code=page.artifact.status_code,
            bytes_received=len(page.artifact.body),
        )
        return page


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


def detail_source(
    source_id: str,
    kind: CollectorKind,
    *,
    entry_url: str,
    config: dict[str, str | int],
) -> SourceDefinition:
    return SourceDefinition(
        source_id=source_id,
        name=f"Fixture {source_id}",
        entry_url=entry_url,
        platforms=(Platform.AMAZON,),
        trust_tier=TrustTier.OFFICIAL,
        collector=kind,
        compliance=ComplianceStatus.ALLOWED,
        enabled=True,
        regions=("global",),
        language_hint="en",
        interval_minutes=120,
        terms_url="https://details.example.com/terms",
        robots_url="https://details.example.com/robots.txt",
        reviewed_at=date(2026, 7, 20),
        compliance_notes="Fixture-only approved detail source.",
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


async def test_two_services_share_atomic_source_lease_without_second_fetch(tmp_path: Path) -> None:
    definition = source("fixture-feed", CollectorKind.RSS)
    release = asyncio.Event()

    class BlockingCollector:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()

        async def collect(self, source_definition, context):
            del source_definition, context
            self.calls += 1
            self.started.set()
            await release.wait()
            if False:
                yield

    collector = BlockingCollector()
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}")
    await database.create_schema()

    def make_service() -> IngestionService:
        return IngestionService(
            registry=SourceRegistry([definition]),
            compliance=CompliancePolicy(),
            collectors={CollectorKind.RSS: collector},
            extractor=ContentExtractor(StaticLanguageDetector()),
            snapshot_store=SnapshotStore(tmp_path / "snapshots"),
            repository=SqlAlchemyIngestionRepository(database.session),
        )

    first_service = make_service()
    second_service = make_service()
    first = asyncio.create_task(first_service.run_source(definition.source_id))
    try:
        await asyncio.wait_for(collector.started.wait(), timeout=1)
        second = await asyncio.wait_for(
            second_service.run_source(definition.source_id),
            timeout=0.5,
        )
        async with database.session() as session:
            active_runs = await session.scalar(
                select(func.count())
                .select_from(FetchRun)
                .where(FetchRun.status == "running")
            )

        assert second.status is RunStatus.SKIPPED
        assert second.error_code == "source_already_running"
        assert collector.calls == 1
        assert active_runs == 1
    finally:
        release.set()
        await first
        await database.dispose()


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
        assert (
            not_modified.http_requests,
            not_modified.http_not_modified,
            not_modified.bytes_received,
        ) == (1, 1, 0)
        assert not_modified.error_summary is None
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
        assert (
            latest_run.http_requests,
            latest_run.http_not_modified,
            latest_run.bytes_received,
        ) == (1, 1, 0)
        assert latest_run.error_summary is None
        snapshot_bodies = {
            gzip.decompress(path.read_bytes())
            for path in (tmp_path / "snapshots").rglob("*.bin.gz")
        }
        assert snapshot_bodies == {
            original_feed,
            changed_feed,
            (FIXTURES / "api.json").read_bytes(),
        }
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
        assert by_source["fixture-api"].error_summary == "fetch_failed"
        assert (
            by_source["fixture-api"].http_requests,
            by_source["fixture-api"].http_not_modified,
            by_source["fixture-api"].bytes_received,
        ) == (1, 0, len(b'{"error":"fixture failure"}'))
        assert by_source["fixture-feed"].status is RunStatus.SUCCESS
        assert by_source["fixture-feed"].created == 2
        async with database.session() as session:
            document_count = await session.scalar(select(func.count()).select_from(Document))
            run_count = await session.scalar(select(func.count()).select_from(FetchRun))
            failed_run = await session.scalar(
                select(FetchRun).where(FetchRun.source_id == api_source.source_id)
            )
        assert document_count == 2
        assert run_count == 2
        assert failed_run is not None
        assert (
            failed_run.http_requests,
            failed_run.http_not_modified,
            failed_run.bytes_received,
            failed_run.error_summary,
        ) == (1, 0, len(b'{"error":"fixture failure"}'), "fetch_failed")
    finally:
        await database.dispose()


async def test_detail_collectors_fetch_extractable_bodies_before_service_persistence(
    tmp_path: Path,
) -> None:
    article = (FIXTURES / "article_en.html").read_bytes()
    html_list = "https://details.example.com/html-list"
    html_failed = "https://details.example.com/html-failed"
    html_detail = "https://details.example.com/html-detail"
    sitemap = "https://details.example.com/sitemap.xml"
    sitemap_failed = "https://details.example.com/sitemap-failed"
    sitemap_detail = "https://details.example.com/sitemap-detail"
    browser_list = "https://details.example.com/browser-list"
    browser_failed = "https://details.example.com/browser-failed"
    browser_detail = "https://details.example.com/browser-detail"
    definitions = [
        detail_source(
            "detail-html",
            CollectorKind.HTML,
            entry_url=html_list,
            config={"link_selector": "main a.item", "item_limit": 2},
        ),
        detail_source(
            "detail-sitemap",
            CollectorKind.SITEMAP,
            entry_url=sitemap,
            config={"item_limit": 2},
        ),
        detail_source(
            "detail-browser",
            CollectorKind.BROWSER,
            entry_url=browser_list,
            config={"link_selector": "main a.item", "item_limit": 2},
        ),
    ]
    http = FixtureHttp(
        {
            html_list: FetchResponse(
                url=html_list,
                status_code=200,
                headers={"content-type": "text/html"},
                body=(
                    b'<main><a class="item" href="/html-failed">Failed</a>'
                    b'<a class="item" href="/html-detail">HTML</a></main>'
                ),
            ),
            html_failed: FetchResponse(
                url=html_failed,
                status_code=500,
                headers={"content-type": "text/html"},
                body=b"",
            ),
            html_detail: FetchResponse(
                url=html_detail,
                status_code=200,
                headers={"content-type": "text/html"},
                body=article,
            ),
            sitemap: FetchResponse(
                url=sitemap,
                status_code=200,
                headers={"content-type": "application/xml"},
                body=(
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    f"<url><loc>{sitemap_failed}</loc></url>"
                    f"<url><loc>{sitemap_detail}</loc></url></urlset>"
                ).encode(),
            ),
            sitemap_failed: FetchResponse(
                url=sitemap_failed,
                status_code=500,
                headers={"content-type": "text/html"},
                body=b"",
            ),
            sitemap_detail: FetchResponse(
                url=sitemap_detail,
                status_code=200,
                headers={"content-type": "text/html"},
                body=article,
            ),
        }
    )
    browser = FixtureBrowser(
        {
            browser_list: RenderedPage(
                url=browser_list,
                body=(
                    b'<main><a class="item" href="/browser-failed">Failed</a>'
                    b'<a class="item" href="/browser-detail">Browser</a></main>'
                ),
                artifact=ResponseArtifact(
                    url=browser_list,
                    status_code=200,
                    headers={"content-type": "text/html"},
                    body=b"raw browser list",
                ),
            ),
            browser_failed: RenderedPage(
                url=browser_failed,
                body=b"failed browser response",
                artifact=ResponseArtifact(
                    url=browser_failed,
                    status_code=500,
                    headers={"content-type": "text/html"},
                    body=b"",
                ),
            ),
            browser_detail: RenderedPage(
                url=browser_detail,
                body=article,
                artifact=ResponseArtifact(
                    url=browser_detail,
                    status_code=200,
                    headers={"content-type": "text/html"},
                    body=b"raw browser detail",
                ),
            ),
        }
    )
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'detail-pipeline.db'}")
    await database.create_schema()
    ingestion = IngestionService(
        registry=SourceRegistry(definitions),
        compliance=CompliancePolicy(),
        collectors={
            CollectorKind.HTML: HtmlCollector(http),
            CollectorKind.SITEMAP: SitemapCollector(http),
            CollectorKind.BROWSER: BrowserCollector(enabled=True, browser_port=browser),
        },
        extractor=ContentExtractor(StaticLanguageDetector()),
        snapshot_store=SnapshotStore(tmp_path / "detail-snapshots"),
        repository=SqlAlchemyIngestionRepository(database.session),
        max_concurrency=3,
    )
    try:
        summaries = await ingestion.run_all(Trigger.MANUAL)

        assert all(summary.status is RunStatus.PARTIAL for summary in summaries)
        assert all(
            (summary.discovered, summary.created, summary.failed) == (2, 1, 1)
            for summary in summaries
        )
        assert all(summary.error_code == "fetch_failed" for summary in summaries)
        assert all(summary.http_requests == 3 for summary in summaries)
        async with database.session() as session:
            document_count = await session.scalar(select(func.count()).select_from(Document))
            version_count = await session.scalar(
                select(func.count()).select_from(DocumentVersion)
            )
        assert (document_count, version_count) == (3, 3)
        requested_urls = [request.url for request in http.requests]
        assert set(requested_urls) == {
            html_list,
            html_failed,
            html_detail,
            sitemap,
            sitemap_failed,
            sitemap_detail,
        }
        assert requested_urls.index(html_failed) < requested_urls.index(html_detail)
        assert requested_urls.index(sitemap_failed) < requested_urls.index(sitemap_detail)
        assert [request.url for request in browser.requests] == [
            browser_list,
            browser_failed,
            browser_detail,
        ]
        snapshot_bodies = {
            gzip.decompress(path.read_bytes())
            for path in (tmp_path / "detail-snapshots").rglob("*.bin.gz")
        }
        assert b"raw browser detail" in snapshot_bodies
        assert b"raw browser list" not in snapshot_bodies
        assert b"failed browser response" not in snapshot_bodies
    finally:
        await database.dispose()


async def test_sitemap_failure_events_respect_candidate_cap_in_service(tmp_path: Path) -> None:
    root = "https://details.example.com/capped-sitemap.xml"
    details = [f"https://details.example.com/capped/{name}" for name in ("a", "b", "c")]
    definition = detail_source(
        "capped-sitemap",
        CollectorKind.SITEMAP,
        entry_url=root,
        config={"item_limit": 2},
    )
    sitemap = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{url}</loc></url>" for url in details)
        + "</urlset>"
    ).encode()
    http = FixtureHttp(
        {
            root: FetchResponse(root, 200, body=sitemap),
            details[0]: FetchResponse(details[0], 500),
            details[1]: FetchResponse(details[1], 500),
            details[2]: FetchResponse(
                details[2],
                200,
                headers={"content-type": "text/html"},
                body=(FIXTURES / "article_en.html").read_bytes(),
            ),
        }
    )
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'capped-sitemap.db'}")
    await database.create_schema()
    ingestion = IngestionService(
        registry=SourceRegistry([definition]),
        compliance=CompliancePolicy(),
        collectors={CollectorKind.SITEMAP: SitemapCollector(http)},
        extractor=ContentExtractor(StaticLanguageDetector()),
        snapshot_store=SnapshotStore(tmp_path / "capped-sitemap-snapshots"),
        repository=SqlAlchemyIngestionRepository(database.session),
    )
    try:
        summary = await ingestion.run_source(definition.source_id, Trigger.MANUAL)

        assert summary.status is RunStatus.FAILED
        assert (summary.discovered, summary.failed, summary.created) == (2, 2, 0)
        assert summary.http_requests == 3
        assert [request.url for request in http.requests] == [root, *details[:2]]
        async with database.session() as session:
            document_count = await session.scalar(select(func.count()).select_from(Document))
            run = await session.scalar(select(FetchRun))
        assert document_count == 0
        assert run is not None
        assert (run.discovered, run.failed, run.created, run.status) == (
            2,
            2,
            0,
            RunStatus.FAILED.value,
        )
    finally:
        await database.dispose()
