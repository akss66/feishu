from __future__ import annotations

from datetime import UTC, date, datetime

from commerce_agent.ingestion.collectors import CollectorError
from commerce_agent.ingestion.collectors.firecrawl import (
    FirecrawlFirstCollector,
    wrap_collectors_with_firecrawl,
)
from commerce_agent.ingestion.models import (
    CollectedFailure,
    CollectedItem,
    CollectorKind,
    ComplianceStatus,
    FetchContext,
    Platform,
    ResponseArtifact,
    SourceDefinition,
    Trigger,
    TrustTier,
)
from commerce_agent.integrations.firecrawl import FirecrawlDocument, FirecrawlError

NOW = datetime(2026, 8, 17, tzinfo=UTC)


def source() -> SourceDefinition:
    return SourceDefinition(
        source_id="amazon-news",
        name="Amazon news",
        entry_url="https://seller.example.com/news",
        platforms=(Platform.AMAZON,),
        trust_tier=TrustTier.OFFICIAL,
        collector=CollectorKind.RSS,
        compliance=ComplianceStatus.ALLOWED,
        enabled=True,
        regions=("global",),
        language_hint="en",
        interval_minutes=120,
        terms_url="https://seller.example.com/terms",
        robots_url="https://seller.example.com/robots.txt",
        reviewed_at=date(2026, 8, 17),
        compliance_notes="Public source.",
    )


def context() -> FetchContext:
    return FetchContext(trigger=Trigger.MANUAL, started_at=NOW)


def native_item() -> CollectedItem:
    body = b"native article"
    url = "https://seller.example.com/news/article"
    return CollectedItem(
        url=url,
        body=body,
        content_type="text/plain",
        artifact=ResponseArtifact(
            url=url,
            status_code=200,
            headers={"content-type": "text/plain"},
            body=body,
        ),
    )


class Firecrawl:
    def __init__(
        self,
        document: FirecrawlDocument | None = None,
        error: FirecrawlError | None = None,
    ) -> None:
        self.document = document or FirecrawlDocument(
            url="https://seller.example.com/news",
            markdown="# Firecrawl page\n\nRecovered policy update.",
            title="Firecrawl page",
            status_code=200,
        )
        self.error = error
        self.calls: list[str] = []

    async def scrape(self, url: str) -> FirecrawlDocument:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.document


class Collector:
    def __init__(self, items=(), *, error: Exception | None = None) -> None:
        self.items = tuple(items)
        self.error = error
        self.calls = 0

    async def collect(self, definition: SourceDefinition, fetch_context: FetchContext):
        del definition, fetch_context
        self.calls += 1
        for item in self.items:
            yield item
        if self.error is not None:
            raise self.error


async def collected(collector: FirecrawlFirstCollector):
    return [item async for item in collector.collect(source(), context())]


async def test_calls_firecrawl_once_but_keeps_native_items_authoritative() -> None:
    firecrawl = Firecrawl()
    fallback = Collector([native_item()])

    items = await collected(FirecrawlFirstCollector(firecrawl, fallback))

    assert firecrawl.calls == ["https://seller.example.com/news"]
    assert fallback.calls == 1
    assert items == [native_item()]


async def test_uses_firecrawl_document_when_native_collector_has_no_item() -> None:
    firecrawl = Firecrawl()

    items = await collected(FirecrawlFirstCollector(firecrawl, Collector()))

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, CollectedItem)
    assert item.url == "https://seller.example.com/news"
    assert item.title == "Firecrawl page"
    assert item.body.startswith(b"# Firecrawl page")
    assert item.content_type == "text/markdown; charset=utf-8"
    assert item.artifact is not None
    assert item.artifact.body == item.body


async def test_suppresses_native_failure_when_firecrawl_recovers_the_source() -> None:
    failure = CollectedFailure("destination_not_public")

    items = await collected(FirecrawlFirstCollector(Firecrawl(), Collector([failure])))

    assert len(items) == 1
    assert isinstance(items[0], CollectedItem)


async def test_firecrawl_failure_is_visible_and_native_collector_still_runs() -> None:
    firecrawl = Firecrawl(error=FirecrawlError("firecrawl_rate_limited"))
    fallback = Collector([native_item()])

    items = await collected(FirecrawlFirstCollector(firecrawl, fallback))

    assert items == [CollectedFailure("firecrawl_rate_limited"), native_item()]
    assert fallback.calls == 1


async def test_native_exception_is_suppressed_when_firecrawl_recovers_the_source() -> None:
    collector = FirecrawlFirstCollector(
        Firecrawl(),
        Collector(error=CollectorError("fetch_failed")),
    )
    items = await collected(collector)

    assert len(items) == 1
    assert isinstance(items[0], CollectedItem)


async def test_native_failure_remains_visible_when_native_item_is_authoritative() -> None:
    failure = CollectedFailure("partial_native_failure")

    items = await collected(
        FirecrawlFirstCollector(Firecrawl(), Collector([native_item(), failure]))
    )

    assert items == [native_item(), failure]


def test_wraps_every_configured_collector_only_when_firecrawl_is_present() -> None:
    native = {
        CollectorKind.RSS: Collector(),
        CollectorKind.API: Collector(),
    }
    firecrawl = Firecrawl()

    wrapped = wrap_collectors_with_firecrawl(native, firecrawl)

    assert set(wrapped) == set(native)
    assert all(isinstance(candidate, FirecrawlFirstCollector) for candidate in wrapped.values())
    assert wrap_collectors_with_firecrawl(native, None) == native
    assert wrap_collectors_with_firecrawl(native, None) is not native
