from __future__ import annotations

import ast
import importlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import pytest

from commerce_agent.ingestion.collectors import (
    ApiCollector,
    BrowserCollector,
    BrowserRequest,
    Collector,
    CollectorError,
    FeedCollector,
    HtmlCollector,
    PlaywrightBrowserPort,
    RenderedPage,
    SitemapCollector,
)
from commerce_agent.ingestion.collectors.base import candidate_url
from commerce_agent.ingestion.http import FetchError, FetchRequest, FetchResponse
from commerce_agent.ingestion.models import (
    CollectedFailure,
    CollectedItem,
    CollectorKind,
    ComplianceStatus,
    ContentScope,
    FetchContext,
    FetchMetrics,
    Platform,
    ResponseArtifact,
    SourceAdapter,
    SourceDefinition,
    Trigger,
    TrustTier,
)
from commerce_agent.ingestion.registry import SourceRegistry
from commerce_agent.ingestion.security import UrlSafetyPolicy
from commerce_agent.media.catalog import ArticleAccess, MediaCategory, PublisherProfile

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ingestion"
PUBLIC_SOURCES = (
    Path(__file__).parents[2] / "src" / "commerce_agent" / "sources" / "public_sources.yaml"
)


class FakeHttpPort:
    def __init__(self, responses: dict[str, bytes | FetchResponse]) -> None:
        self.responses = responses
        self.requests: list[FetchRequest] = []

    async def get(self, request: FetchRequest) -> FetchResponse:
        self.requests.append(request)
        response = self.responses[request.url]
        if isinstance(response, FetchResponse):
            result = response
        else:
            result = FetchResponse(
                url=request.url,
                status_code=200,
                headers={"content-type": "application/octet-stream"},
                body=response,
            )
        if request.metrics is not None:
            request.metrics.record_request(
                status_code=result.status_code,
                bytes_received=len(result.body),
            )
        return result


class FakeBrowserPort:
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


class FakeResolver:
    def __init__(self, addresses: dict[str, tuple[str, ...]]) -> None:
        self.addresses = addresses
        self.calls: list[str] = []

    async def __call__(self, host: str) -> tuple[str, ...]:
        self.calls.append(host)
        return self.addresses[host]


def source(
    kind: CollectorKind,
    *,
    entry_url: str,
    config: dict[str, str | int] | None = None,
) -> SourceDefinition:
    return SourceDefinition(
        source_id=f"fixture-{kind.value}",
        name=f"Fixture {kind.value}",
        entry_url=entry_url,
        platforms=(Platform.AMAZON,),
        trust_tier=TrustTier.OFFICIAL,
        collector=kind,
        compliance=ComplianceStatus.ALLOWED,
        enabled=True,
        regions=("global",),
        language_hint="en",
        interval_minutes=120,
        terms_url="https://example.com/terms",
        robots_url="https://example.com/robots.txt",
        reviewed_at=date(2026, 7, 20),
        compliance_notes="Offline collector fixture.",
        collector_config=config or {},
    )


def context() -> FetchContext:
    return FetchContext(
        trigger=Trigger.MANUAL,
        started_at=datetime(2026, 7, 20, tzinfo=UTC),
        etag='"previous"',
        last_modified="Sun, 19 Jul 2026 08:00:00 GMT",
    )


async def collected(
    collector: Collector,
    definition: SourceDefinition,
    fetch_context: FetchContext | None = None,
):
    return [item async for item in collector.collect(definition, fetch_context or context())]


def bundled_xml(name: str) -> dict[str, bytes]:
    root = ElementTree.fromstring((FIXTURES / name).read_bytes())
    return {
        node.attrib.get("name", node.attrib.get("url", "")): (node.text or "").strip().encode()
        for node in root.findall("document")
    }


def test_importing_collectors_does_not_import_playwright() -> None:
    assert "playwright" not in sys.modules
    assert "playwright.async_api" not in sys.modules


def test_response_artifact_is_immutable_and_filters_unsafe_headers() -> None:
    raw_headers = {
        "Content-Type": "text/html; charset=utf-8",
        "ETag": '"v1"',
        "Last-Modified": "Mon, 20 Jul 2026 08:00:00 GMT",
        "Set-Cookie": "session=never-store",
        "Authorization": "Bearer never-store",
    }
    raw_body = bytearray(b"raw response")

    artifact = ResponseArtifact(
        url="https://example.com/article?token=not-metadata",
        status_code=200,
        headers=raw_headers,
        body=raw_body,
    )
    item = CollectedItem(url="https://example.com/article", body=b"rendered", artifact=artifact)
    raw_headers["Content-Type"] = "application/json"
    raw_body[:] = b"changed"

    assert dict(artifact.headers) == {
        "content-type": "text/html; charset=utf-8",
        "etag": '"v1"',
        "last-modified": "Mon, 20 Jul 2026 08:00:00 GMT",
    }
    assert artifact.body == b"raw response"
    assert item.artifact is artifact
    with pytest.raises(TypeError):
        artifact.headers["content-type"] = "application/json"  # type: ignore[index]


def test_fetch_metrics_are_isolated_mutable_and_reject_unsafe_counters() -> None:
    first = context()
    second = context()

    first.metrics.record_request(status_code=200, bytes_received=12)
    first.metrics.record_request(status_code=304, bytes_received=0)
    first.metrics.record_request(status_code=None, bytes_received=0)

    assert first.metrics == FetchMetrics(
        http_requests=3,
        http_not_modified=1,
        bytes_received=12,
    )
    assert second.metrics == FetchMetrics()
    with pytest.raises(ValueError):
        FetchMetrics(http_requests=-1)
    with pytest.raises(TypeError):
        FetchMetrics(bytes_received=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        first.metrics.record_request(status_code=200, bytes_received=-1)
    saturated = FetchMetrics(bytes_received=2**63 - 1)
    with pytest.raises(ValueError):
        saturated.record_request(status_code=200, bytes_received=1)
    assert saturated == FetchMetrics(bytes_received=2**63 - 1)

    concurrent = FetchMetrics()
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda _: concurrent.record_request(
                    status_code=200,
                    bytes_received=1,
                ),
                range(8_000),
            )
        )
    assert concurrent == FetchMetrics(http_requests=8_000, bytes_received=8_000)


@pytest.mark.asyncio
async def test_feed_collector_parses_rss_and_atom_from_injected_http_port() -> None:
    documents = bundled_xml("feed.xml")
    cases = [
        (
            "rss",
            "https://feeds.example.com/feed.xml",
            [
                "https://feeds.example.com/news/fee-update",
                "https://feeds.example.com/news/shipping",
            ],
            "Seller fee update",
            "newsroom@example.com",
        ),
        (
            "atom",
            "https://atom.example.com/feed.xml",
            ["https://atom.example.com/policy/2026-07", "https://atom.example.com/operations"],
            "Policy update",
            "Policy Team",
        ),
    ]

    for document_name, entry_url, expected_urls, expected_title, expected_author in cases:
        http = FakeHttpPort({entry_url: documents[document_name]})
        definition = source(CollectorKind.RSS, entry_url=entry_url, config={"item_limit": 10})

        items = await collected(FeedCollector(http), definition)

        assert [item.url for item in items] == expected_urls
        assert items[0].title == expected_title
        assert items[0].author == expected_author
        assert items[0].published_at is not None
        assert items[0].published_at.tzinfo is not None
        assert all(item.artifact is not None for item in items)
        assert all(
            item.artifact.body == documents[document_name] for item in items if item.artifact
        )
        assert all(item.artifact.status_code == 200 for item in items if item.artifact)
        assert len(http.requests) == 1
        assert http.requests[0] == FetchRequest(
            url=entry_url,
            allowed_hosts=(entry_url.split("/")[2],),
            etag='"previous"',
            last_modified="Sun, 19 Jul 2026 08:00:00 GMT",
        )


@pytest.mark.asyncio
async def test_feed_collector_applies_cap_after_removing_duplicates() -> None:
    documents = bundled_xml("feed.xml")
    url = "https://feeds.example.com/feed.xml"
    http = FakeHttpPort({url: documents["rss"]})
    definition = source(CollectorKind.RSS, entry_url=url, config={"item_limit": 2})

    items = await collected(FeedCollector(http), definition)

    assert [item.url for item in items] == [
        "https://feeds.example.com/news/fee-update",
        "https://feeds.example.com/news/shipping",
    ]


@pytest.mark.asyncio
async def test_feed_collector_fetches_details_and_keeps_only_matching_terms() -> None:
    feed_url = "https://feeds.example.com/feed.xml"
    matching_url = "https://feeds.example.com/recalls/temu-item"
    unrelated_url = "https://feeds.example.com/recalls/other-item"
    feed = f"""
    <rss version="2.0"><channel>
      <item><title>Children's bracelet recalled</title><link>{matching_url}</link></item>
      <item><title>Kitchen appliance recalled</title><link>{unrelated_url}</link></item>
    </channel></rss>
    """.encode()
    matching_detail = b"<article>Sold online through Whaleco Canada Inc. (Temu).</article>"
    unrelated_detail = b"<article>Sold by an unrelated Canadian retailer.</article>"
    http = FakeHttpPort(
        {
            feed_url: feed,
            matching_url: FetchResponse(
                url=matching_url,
                status_code=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=matching_detail,
            ),
            unrelated_url: FetchResponse(
                url=unrelated_url,
                status_code=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=unrelated_detail,
            ),
        }
    )
    definition = source(
        CollectorKind.RSS,
        entry_url=feed_url,
        config={"item_limit": 10, "detail_match_terms": "temu|whaleco"},
    )

    items = await collected(FeedCollector(http), definition)

    assert [item.url for item in items] == [matching_url]
    assert items[0].body == matching_detail
    assert items[0].artifact is not None
    assert items[0].artifact.body == matching_detail
    assert [request.url for request in http.requests] == [
        feed_url,
        matching_url,
        unrelated_url,
    ]
    assert http.requests[1].etag is None
    assert http.requests[1].last_modified is None


@pytest.mark.asyncio
async def test_feed_collector_filters_entries_without_fetching_details() -> None:
    feed_url = "https://feeds.example.com/feed.xml"
    matching_url = "https://feeds.example.com/news/temu"
    feed = f"""
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Canada reviews Whaleco marketplace practices</title>
        <link href="{matching_url}"/><summary>Competition update</summary></entry>
      <entry><title>Unrelated merger review</title>
        <link href="https://feeds.example.com/news/other"/></entry>
    </feed>
    """.encode()
    http = FakeHttpPort({feed_url: feed})
    definition = source(
        CollectorKind.RSS,
        entry_url=feed_url,
        config={"item_limit": 10, "entry_match_terms": "temu|whaleco"},
    )

    items = await collected(FeedCollector(http), definition)

    assert [item.url for item in items] == [matching_url]
    assert len(http.requests) == 1


@pytest.mark.asyncio
async def test_feed_collector_records_a_not_modified_response_once() -> None:
    url = "https://feeds.example.com/feed.xml"
    http = FakeHttpPort(
        {
            url: FetchResponse(
                url=url,
                status_code=304,
                headers={"etag": '"current"'},
                body=b"",
            )
        }
    )
    definition = source(CollectorKind.RSS, entry_url=url, config={"item_limit": 10})
    fetch_context = context()

    items = await collected(FeedCollector(http), definition, fetch_context)

    assert items == []
    assert fetch_context.metrics == FetchMetrics(
        http_requests=1,
        http_not_modified=1,
        bytes_received=0,
    )


@pytest.mark.asyncio
async def test_sitemap_collector_walks_nested_indexes_and_namespaced_urlsets() -> None:
    documents = bundled_xml("sitemap.xml")
    detail_body = (FIXTURES / "article_en.html").read_bytes()
    for url in (
        "https://docs.example.com/articles/alpha",
        "https://docs.example.com/articles/shared",
        "https://docs.example.com/articles/bravo",
    ):
        documents[url] = detail_body
    http = FakeHttpPort(documents)
    definition = source(
        CollectorKind.SITEMAP,
        entry_url="https://docs.example.com/sitemap.xml",
        config={"item_limit": 10},
    )

    fetch_context = context()
    items = await collected(SitemapCollector(http), definition, fetch_context)

    assert [item.url for item in items] == [
        "https://docs.example.com/articles/alpha",
        "https://docs.example.com/articles/shared",
        "https://docs.example.com/articles/bravo",
    ]
    assert all(item.body == detail_body for item in items)
    assert all(item.artifact is not None and item.artifact.body == detail_body for item in items)
    assert [request.url for request in http.requests] == [
        "https://docs.example.com/sitemap.xml",
        "https://docs.example.com/sitemaps/nested.xml",
        "https://docs.example.com/sitemaps/articles-a.xml",
        "https://docs.example.com/articles/alpha",
        "https://docs.example.com/articles/shared",
        "https://docs.example.com/sitemaps/articles-b.xml",
        "https://docs.example.com/articles/bravo",
    ]
    assert all(request.allowed_hosts == ("docs.example.com",) for request in http.requests)
    assert http.requests[0].etag == '"previous"'
    assert all(request.etag is None for request in http.requests[1:])
    assert fetch_context.metrics.http_requests == 7
    assert fetch_context.metrics.bytes_received > len(detail_body) * 3


@pytest.mark.asyncio
async def test_sitemap_collector_caps_unique_candidates() -> None:
    documents = bundled_xml("sitemap.xml")
    detail_body = (FIXTURES / "article_en.html").read_bytes()
    documents.update(
        {
            "https://docs.example.com/articles/alpha": detail_body,
            "https://docs.example.com/articles/shared": detail_body,
        }
    )
    definition = source(
        CollectorKind.SITEMAP,
        entry_url="https://docs.example.com/sitemap.xml",
        config={"item_limit": 2},
    )

    http = FakeHttpPort(documents)
    items = await collected(SitemapCollector(http), definition)

    assert [item.url for item in items] == [
        "https://docs.example.com/articles/alpha",
        "https://docs.example.com/articles/shared",
    ]
    assert [request.url for request in http.requests] == [
        "https://docs.example.com/sitemap.xml",
        "https://docs.example.com/sitemaps/nested.xml",
        "https://docs.example.com/sitemaps/articles-a.xml",
        "https://docs.example.com/articles/alpha",
        "https://docs.example.com/articles/shared",
    ]


@pytest.mark.asyncio
async def test_sitemap_collector_caps_consecutive_detail_failures() -> None:
    root = "https://docs.example.com/sitemap.xml"
    details = [f"https://docs.example.com/articles/{name}" for name in ("a", "b", "c")]
    sitemap = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{url}</loc></url>" for url in details)
        + "</urlset>"
    ).encode()
    http = FakeHttpPort(
        {
            root: sitemap,
            details[0]: FetchResponse(details[0], 500),
            details[1]: FetchResponse(details[1], 500),
            details[2]: (FIXTURES / "article_en.html").read_bytes(),
        }
    )
    definition = source(
        CollectorKind.SITEMAP,
        entry_url=root,
        config={"item_limit": 2},
    )

    items = await collected(SitemapCollector(http), definition)

    assert items == [CollectedFailure("fetch_failed"), CollectedFailure("fetch_failed")]
    assert [request.url for request in http.requests] == [root, *details[:2]]


@pytest.mark.asyncio
async def test_sitemap_collector_stops_a_cycle_without_refetching() -> None:
    root = "https://docs.example.com/sitemap.xml"
    nested = "https://docs.example.com/nested.xml"
    http = FakeHttpPort(
        {
            root: _sitemap_index(nested),
            nested: _sitemap_index(root),
        }
    )
    definition = source(CollectorKind.SITEMAP, entry_url=root, config={"item_limit": 10})

    items = await collected(SitemapCollector(http), definition)

    assert items == []
    assert [request.url for request in http.requests] == [root, nested]


@pytest.mark.asyncio
async def test_sitemap_collector_rejects_more_than_256_sitemap_documents() -> None:
    urls = [f"https://docs.example.com/sitemap-{index}.xml" for index in range(257)]
    responses = {url: _sitemap_index(urls[index + 1]) for index, url in enumerate(urls[:-1])}
    http = FakeHttpPort(responses)
    definition = source(
        CollectorKind.SITEMAP,
        entry_url=urls[0],
        config={"item_limit": 10},
    )

    with pytest.raises(CollectorError) as error:
        await collected(SitemapCollector(http), definition)

    assert error.value.code == "item_limit_exceeded"
    assert len(http.requests) == 256


def _sitemap_index(url: str) -> bytes:
    return (
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<sitemap><loc>{url}</loc></sitemap>"
        "</sitemapindex>"
    ).encode()


@pytest.mark.asyncio
async def test_html_collector_uses_selector_resolves_links_and_deduplicates() -> None:
    url = "https://news.example.com/list/index.html"
    response = FetchResponse(
        url="https://news.example.com/updates/",
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=(FIXTURES / "list.html").read_bytes(),
    )
    detail_body = (FIXTURES / "article_en.html").read_bytes()
    alpha_url = "https://news.example.com/articles/alpha"
    bravo_url = "https://news.example.com/updates/articles/bravo"
    http = FakeHttpPort(
        {
            url: response,
            alpha_url: FetchResponse(
                url=alpha_url,
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8", "Set-Cookie": "discard"},
                body=detail_body,
            ),
            bravo_url: FetchResponse(
                url=bravo_url,
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=detail_body,
            ),
        }
    )
    definition = source(
        CollectorKind.HTML,
        entry_url=url,
        config={"link_selector": "main#updates article a.update-link", "item_limit": 2},
    )

    fetch_context = context()
    items = await collected(HtmlCollector(http), definition, fetch_context)

    assert [item.url for item in items] == [
        "https://news.example.com/articles/alpha",
        "https://news.example.com/updates/articles/bravo",
    ]
    assert [item.title for item in items] == ["Alpha", "Bravo"]
    assert all(item.body == detail_body for item in items)
    assert all(item.artifact is not None and item.artifact.body == detail_body for item in items)
    assert [request.url for request in http.requests] == [url, alpha_url, bravo_url]
    assert all(request.allowed_hosts == ("news.example.com",) for request in http.requests)
    assert http.requests[0].etag == '"previous"'
    assert all(request.etag is None for request in http.requests[1:])
    assert "set-cookie" not in items[0].artifact.headers
    assert fetch_context.metrics == FetchMetrics(
        http_requests=3,
        bytes_received=len(response.body) + len(detail_body) * 2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code",
    ["compliance_review_required", "rate_limited"],
)
async def test_html_collector_stops_after_terminal_detail_failure(
    error_code: str,
) -> None:
    entry_url = "https://news.example.com/list"
    first_url = "https://news.example.com/first"
    second_url = "https://news.example.com/second"

    class TerminalHttpPort:
        def __init__(self) -> None:
            self.requests: list[FetchRequest] = []

        async def get(self, request: FetchRequest) -> FetchResponse:
            self.requests.append(request)
            if request.url == entry_url:
                return FetchResponse(
                    entry_url,
                    200,
                    body=(
                        f'<a class="item" href="{first_url}">First</a>'
                        f'<a class="item" href="{second_url}">Second</a>'
                    ).encode(),
                )
            if request.url == first_url:
                raise FetchError(error_code)
            raise AssertionError("terminal detail failure must stop later requests")

    http = TerminalHttpPort()
    definition = source(
        CollectorKind.HTML,
        entry_url=entry_url,
        config={"link_selector": "a.item", "item_limit": 2},
    )

    results = await collected(HtmlCollector(http), definition)

    assert results == [CollectedFailure(error_code)]
    assert [request.url for request in http.requests] == [entry_url, first_url]


@pytest.mark.asyncio
async def test_ebay_press_room_registry_selector_collects_public_announcement_details() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    source_definition = registry.require("ebay-press-room")
    detail_body = (FIXTURES / "article_en.html").read_bytes()
    company_update_url = "https://www.ebayinc.com/stories/company-update/"
    marketplace_update_url = "https://www.ebayinc.com/stories/marketplace-update/"
    http = FakeHttpPort(
        {
            source_definition.entry_url: FetchResponse(
                url=source_definition.entry_url,
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=(FIXTURES / "ebay_press_room_selector.html").read_bytes(),
            ),
            company_update_url: FetchResponse(
                url=company_update_url,
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=detail_body,
            ),
            marketplace_update_url: FetchResponse(
                url=marketplace_update_url,
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=detail_body,
            ),
        }
    )

    items = await collected(HtmlCollector(http), source_definition, context())

    assert [item.url for item in items] == [
        company_update_url,
        marketplace_update_url,
    ]
    assert [item.title for item in items] == ["Company update", "Marketplace update"]
    assert all(item.body == detail_body for item in items)
    assert [request.url for request in http.requests] == [
        source_definition.entry_url,
        company_update_url,
        marketplace_update_url,
    ]
    assert all(request.allowed_hosts == ("www.ebayinc.com",) for request in http.requests)


@pytest.mark.asyncio
async def test_api_collector_extracts_public_json_path_and_configured_fields() -> None:
    url = "https://api.example.com/v1/public"
    http = FakeHttpPort({url: (FIXTURES / "api.json").read_bytes()})
    definition = source(
        CollectorKind.API,
        entry_url=url,
        config={
            "items_path": "data.items",
            "url_field": "url",
            "title_field": "headline",
            "published_at_field": "published_at",
            "item_limit": 2,
        },
    )

    fetch_context = context()
    items = await collected(ApiCollector(http), definition, fetch_context)

    assert [item.url for item in items] == [
        "https://api.example.com/updates/alpha",
        "https://api.example.com/v1/updates/bravo",
    ]
    assert [item.title for item in items] == ["Alpha update", "Bravo update"]
    assert all(item.published_at is not None for item in items)
    assert json.loads(items[0].body) == {
        "url": "/updates/alpha",
        "headline": "Alpha update",
        "published_at": "2026-07-20T08:00:00Z",
    }
    assert all(item.artifact is not None for item in items)
    assert all(
        item.artifact.body == (FIXTURES / "api.json").read_bytes()
        for item in items
        if item.artifact
    )
    assert fetch_context.metrics == FetchMetrics(
        http_requests=1,
        bytes_received=len((FIXTURES / "api.json").read_bytes()),
    )


@pytest.mark.asyncio
async def test_gdelt_adapter_keeps_safe_article_metadata_and_publisher_identity() -> None:
    url = "https://api.gdeltproject.org/api/v2/doc/doc?query=marketplace"
    http = FakeHttpPort({url: (FIXTURES / "gdelt_articles.json").read_bytes()})
    definition = replace(
        source(
            CollectorKind.API,
            entry_url=url,
            config={
                "items_path": "articles",
                "url_field": "url",
                "title_field": "title",
                "published_at_field": "seendate",
                "publisher_field": "domain",
                "item_limit": 50,
            },
        ),
        trust_tier=TrustTier.MEDIA,
        adapter=SourceAdapter.GDELT,
        content_scope=ContentScope.METADATA_ONLY,
        attribution="GDELT index; original publisher shown per item",
    )

    items = await collected(ApiCollector(http), definition)

    assert len(items) == 1
    assert items[0].url == "https://www.reuters.com/world/example-story/"
    assert items[0].title == "Example marketplace policy story"
    assert items[0].publisher_key == "reuters.com"
    assert items[0].published_at == datetime(2026, 7, 22, 8, 15, tzinfo=UTC)
    assert json.loads(items[0].body) == {
        "domain": "reuters.com",
        "seendate": "20260722T081500Z",
        "title": "Example marketplace policy story",
        "url": "https://www.reuters.com/world/example-story/",
    }


def _fixture_gdelt_source(url: str) -> SourceDefinition:
    return replace(
        source(
            CollectorKind.API,
            entry_url=url,
            config={
                "items_path": "articles",
                "url_field": "url",
                "title_field": "title",
                "published_at_field": "seendate",
                "publisher_field": "domain",
                "item_limit": 50,
            },
        ),
        trust_tier=TrustTier.MEDIA,
        adapter=SourceAdapter.GDELT,
        content_scope=ContentScope.METADATA_ONLY,
        attribution="GDELT index; original publisher shown per item",
    )


@pytest.mark.asyncio
async def test_gdelt_original_article_is_fetched_only_for_allowed_public() -> None:
    discovery_url = "https://api.gdeltproject.org/api/v2/doc/doc?query=marketplace"
    article_url = "https://publisher.example/article"
    discovery_body = json.dumps(
        {
            "articles": [
                {
                    "url": article_url,
                    "title": "Original article",
                    "seendate": "20260722T081500Z",
                    "domain": "publisher.example",
                }
            ]
        }
    ).encode()
    article_body = (
        b"<html><article>"
        + (b"<p>Amazon marketplace sellers must review this policy update carefully.</p>" * 20)
        + b"</article></html>"
    )
    http = FakeHttpPort(
        {
            discovery_url: discovery_body,
            article_url: FetchResponse(
                url=article_url,
                status_code=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=article_body,
            ),
        }
    )
    profile = PublisherProfile(
        publisher_key="publisher.example",
        display_name="Fixture Publisher",
        category=MediaCategory.SPECIALIST,
        article_access=ArticleAccess.ALLOWED_PUBLIC,
        allowed_hosts=("publisher.example",),
    )

    items = await collected(
        ApiCollector(
            http,
            publisher_lookup=lambda _: profile,
            fetch_gdelt_originals=True,
            gdelt_original_fetch_limit=5,
        ),
        _fixture_gdelt_source(discovery_url),
    )

    assert len(http.requests) == 2
    assert http.requests[1].url == article_url
    assert http.requests[1].allowed_hosts == ("publisher.example",)
    assert http.requests[1].etag is None
    assert http.requests[1].last_modified is None
    assert items[0].body == article_body
    assert items[0].content_type == "text/html; charset=utf-8"
    assert items[0].content_scope is ContentScope.FULL_TEXT
    assert items[0].artifact is not None
    assert items[0].artifact.body == article_body


@pytest.mark.asyncio
async def test_gdelt_public_authority_fetches_reviewed_www_host_exactly() -> None:
    discovery_url = "https://api.gdeltproject.org/api/v2/doc/doc?query=amazon"
    article_url = "https://www.ftc.gov/news-events/news/amazon-marketplace"
    discovery_body = json.dumps(
        {
            "articles": [
                {
                    "url": article_url,
                    "title": "Amazon marketplace enforcement update",
                    "seendate": "20260722T081500Z",
                    "domain": "ftc.gov",
                }
            ]
        }
    ).encode()
    article_body = (
        b"<html><article>"
        + b"<p>Amazon marketplace sellers received a public enforcement update.</p>" * 20
        + b"</article></html>"
    )
    http = FakeHttpPort(
        {
            discovery_url: discovery_body,
            article_url: FetchResponse(
                article_url,
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=article_body,
            ),
        }
    )

    items = await collected(
        ApiCollector(http, fetch_gdelt_originals=True),
        _fixture_gdelt_source(discovery_url),
    )

    assert len(items) == 1
    assert items[0].content_scope is ContentScope.FULL_TEXT
    assert http.requests[1].allowed_hosts == ("ftc.gov", "www.ftc.gov")
    assert "news.ftc.gov" not in http.requests[1].allowed_hosts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "access",
    [
        ArticleAccess.AUTHORIZATION_REQUIRED,
        ArticleAccess.METADATA_ONLY,
        ArticleAccess.LICENSED_API,
        ArticleAccess.DENIED,
    ],
)
async def test_gdelt_original_article_is_not_fetched_for_disallowed_access(
    access: ArticleAccess,
) -> None:
    discovery_url = "https://api.gdeltproject.org/api/v2/doc/doc?query=marketplace"
    article_url = "https://publisher.example/article"
    discovery_body = json.dumps(
        {
            "articles": [
                {
                    "url": article_url,
                    "title": "Metadata only article",
                    "seendate": "20260722T081500Z",
                    "domain": "publisher.example",
                }
            ]
        }
    ).encode()
    http = FakeHttpPort({discovery_url: discovery_body})
    profile = PublisherProfile(
        publisher_key="publisher.example",
        display_name="Fixture Publisher",
        category=MediaCategory.SPECIALIST,
        article_access=access,
        allowed_hosts=("publisher.example",),
    )

    items = await collected(
        ApiCollector(
            http,
            publisher_lookup=lambda _: profile,
            fetch_gdelt_originals=True,
        ),
        _fixture_gdelt_source(discovery_url),
    )

    assert len(http.requests) == 1
    if access is ArticleAccess.DENIED:
        assert items == []
    else:
        assert len(items) == 1
        assert items[0].content_scope is ContentScope.METADATA_ONLY
        assert json.loads(items[0].body)["url"] == article_url


@pytest.mark.asyncio
async def test_gdelt_original_fetch_is_disabled_by_default() -> None:
    discovery_url = "https://api.gdeltproject.org/api/v2/doc/doc?query=marketplace"
    article_url = "https://publisher.example/article"
    discovery_body = json.dumps(
        {
            "articles": [
                {
                    "url": article_url,
                    "title": "Amazon policy article",
                    "seendate": "20260722T081500Z",
                    "domain": "publisher.example",
                }
            ]
        }
    ).encode()
    http = FakeHttpPort({discovery_url: discovery_body})
    profile = PublisherProfile(
        publisher_key="publisher.example",
        display_name="Fixture Publisher",
        category=MediaCategory.SPECIALIST,
        article_access=ArticleAccess.ALLOWED_PUBLIC,
        allowed_hosts=("publisher.example",),
    )

    items = await collected(
        ApiCollector(http, publisher_lookup=lambda _: profile),
        _fixture_gdelt_source(discovery_url),
    )

    assert [request.url for request in http.requests] == [discovery_url]
    assert len(items) == 1
    assert items[0].content_scope is ContentScope.METADATA_ONLY


@pytest.mark.asyncio
async def test_gdelt_original_fetch_is_suppressed_during_probe() -> None:
    discovery_url = "https://api.gdeltproject.org/api/v2/doc/doc?query=marketplace"
    article_url = "https://publisher.example/article"
    discovery_body = json.dumps(
        {
            "articles": [
                {
                    "url": article_url,
                    "title": "Amazon policy article",
                    "seendate": "20260722T081500Z",
                    "domain": "publisher.example",
                }
            ]
        }
    ).encode()
    http = FakeHttpPort({discovery_url: discovery_body})
    profile = PublisherProfile(
        publisher_key="publisher.example",
        display_name="Fixture Publisher",
        category=MediaCategory.SPECIALIST,
        article_access=ArticleAccess.ALLOWED_PUBLIC,
        allowed_hosts=("publisher.example",),
    )
    probe_context = replace(context(), allow_original_fetch=False)

    items = await collected(
        ApiCollector(
            http,
            publisher_lookup=lambda _: profile,
            fetch_gdelt_originals=True,
        ),
        _fixture_gdelt_source(discovery_url),
        probe_context,
    )

    assert len(items) == 1
    assert items[0].content_scope is ContentScope.METADATA_ONLY
    assert [request.url for request in http.requests] == [discovery_url]


@pytest.mark.asyncio
async def test_gdelt_original_fetch_budget_degrades_later_items_to_metadata() -> None:
    discovery_url = "https://api.gdeltproject.org/api/v2/doc/doc?query=marketplace"
    first_url = "https://publisher.example/first"
    second_url = "https://publisher.example/second"
    discovery_body = json.dumps(
        {
            "articles": [
                {
                    "url": first_url,
                    "title": "First Amazon article",
                    "seendate": "20260722T081500Z",
                    "domain": "publisher.example",
                },
                {
                    "url": second_url,
                    "title": "Second Amazon article",
                    "seendate": "20260722T081600Z",
                    "domain": "publisher.example",
                },
            ]
        }
    ).encode()
    article_body = (
        b"<html><article>"
        + b"<p>Amazon marketplace policy details for sellers.</p>" * 20
        + b"</article></html>"
    )
    http = FakeHttpPort(
        {
            discovery_url: discovery_body,
            first_url: FetchResponse(
                url=first_url,
                status_code=200,
                headers={"content-type": "text/html"},
                body=article_body,
            ),
        }
    )
    profile = PublisherProfile(
        publisher_key="publisher.example",
        display_name="Fixture Publisher",
        category=MediaCategory.SPECIALIST,
        article_access=ArticleAccess.ALLOWED_PUBLIC,
        allowed_hosts=("publisher.example",),
    )

    items = await collected(
        ApiCollector(
            http,
            publisher_lookup=lambda _: profile,
            fetch_gdelt_originals=True,
            gdelt_original_fetch_limit=1,
        ),
        _fixture_gdelt_source(discovery_url),
    )

    assert [request.url for request in http.requests] == [discovery_url, first_url]
    assert [item.content_scope for item in items] == [
        ContentScope.FULL_TEXT,
        ContentScope.METADATA_ONLY,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "article_response",
    [
        FetchResponse(
            url="https://publisher.example/article",
            status_code=403,
            headers={"content-type": "text/html"},
        ),
        FetchResponse(
            url="https://publisher.example/article",
            status_code=200,
            headers={"content-type": "application/pdf"},
            body=b"%PDF",
        ),
        FetchResponse(
            url="https://publisher.example/article",
            status_code=200,
            headers={"content-type": "text/html"},
            body=b"<html><p>Amazon short page.</p></html>",
        ),
    ],
)
async def test_gdelt_original_fetch_failure_keeps_metadata_lead(
    article_response: FetchResponse,
) -> None:
    discovery_url = "https://api.gdeltproject.org/api/v2/doc/doc?query=marketplace"
    article_url = article_response.url
    discovery_body = json.dumps(
        {
            "articles": [
                {
                    "url": article_url,
                    "title": "Amazon policy article",
                    "seendate": "20260722T081500Z",
                    "domain": "publisher.example",
                }
            ]
        }
    ).encode()
    http = FakeHttpPort(
        {
            discovery_url: discovery_body,
            article_url: article_response,
        }
    )
    profile = PublisherProfile(
        publisher_key="publisher.example",
        display_name="Fixture Publisher",
        category=MediaCategory.SPECIALIST,
        article_access=ArticleAccess.ALLOWED_PUBLIC,
        allowed_hosts=("publisher.example",),
    )

    items = await collected(
        ApiCollector(
            http,
            publisher_lookup=lambda _: profile,
            fetch_gdelt_originals=True,
        ),
        _fixture_gdelt_source(discovery_url),
    )

    assert len(items) == 1
    assert items[0].content_scope is ContentScope.METADATA_ONLY
    assert json.loads(items[0].body)["url"] == article_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code",
    ["compliance_review_required", "rate_limited"],
)
async def test_gdelt_terminal_original_failure_stops_later_original_requests(
    error_code: str,
) -> None:
    discovery_url = "https://api.gdeltproject.org/api/v2/doc/doc?query=marketplace"
    first_url = "https://publisher.example/first"
    second_url = "https://publisher.example/second"
    discovery_body = json.dumps(
        {
            "articles": [
                {
                    "url": first_url,
                    "title": "First Amazon article",
                    "seendate": "20260722T081500Z",
                    "domain": "publisher.example",
                },
                {
                    "url": second_url,
                    "title": "Second Amazon article",
                    "seendate": "20260722T081600Z",
                    "domain": "publisher.example",
                },
            ]
        }
    ).encode()

    class TerminalHttpPort:
        def __init__(self) -> None:
            self.requests: list[FetchRequest] = []

        async def get(self, request: FetchRequest) -> FetchResponse:
            self.requests.append(request)
            if request.url == discovery_url:
                return FetchResponse(discovery_url, 200, body=discovery_body)
            if request.url == first_url:
                raise FetchError(error_code)
            raise AssertionError("terminal publisher failure must stop later originals")

    http = TerminalHttpPort()
    profile = PublisherProfile(
        publisher_key="publisher.example",
        display_name="Fixture Publisher",
        category=MediaCategory.SPECIALIST,
        article_access=ArticleAccess.ALLOWED_PUBLIC,
        allowed_hosts=("publisher.example",),
    )

    results = await collected(
        ApiCollector(
            http,
            publisher_lookup=lambda _: profile,
            fetch_gdelt_originals=True,
        ),
        _fixture_gdelt_source(discovery_url),
    )

    failures = [result.error_code for result in results if isinstance(result, CollectedFailure)]
    metadata = [result for result in results if isinstance(result, CollectedItem)]
    assert failures == [error_code]
    assert len(metadata) == 2
    assert all(item.content_scope is ContentScope.METADATA_ONLY for item in metadata)
    assert [request.url for request in http.requests] == [discovery_url, first_url]


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://localhost/private",
        "https://sub.localhost/private",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://10.0.0.1/private",
        "https://[::1]/private",
        "https://[fe80::1]/private",
        "https://user:password@example.com/private",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ],
)
def test_candidate_url_rejects_unsafe_discovered_targets(raw_url: str) -> None:
    assert candidate_url("https://public.example/news", raw_url) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("collector_factory", "kind", "config", "payload"),
    [
        (FeedCollector, CollectorKind.RSS, {}, b"<rss><broken>"),
        (SitemapCollector, CollectorKind.SITEMAP, {}, b"<urlset><broken>"),
        (
            ApiCollector,
            CollectorKind.API,
            {"items_path": "data.items", "url_field": "url"},
            b"{not-json}",
        ),
        (
            ApiCollector,
            CollectorKind.API,
            {"items_path": "data.items", "url_field": "url"},
            b'{"data": {"items": {"url": "/one"}}}',
        ),
    ],
)
async def test_structured_collectors_classify_invalid_payloads(
    collector_factory: type,
    kind: CollectorKind,
    config: dict[str, str],
    payload: bytes,
) -> None:
    url = "https://payload.example.com/source"
    definition = source(kind, entry_url=url, config=config)

    with pytest.raises(CollectorError) as error:
        await collected(collector_factory(FakeHttpPort({url: payload})), definition)

    assert error.value.code == "invalid_payload"


def test_collectors_do_not_import_persistence() -> None:
    collector_dir = (
        Path(__file__).parents[2] / "src" / "commerce_agent" / "ingestion" / "collectors"
    )
    imported_modules: set[str] = set()
    for path in collector_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

    assert not any(module.startswith("commerce_agent.persistence") for module in imported_modules)


@pytest.mark.asyncio
async def test_browser_collector_uses_injected_renderer_for_candidate_links() -> None:
    list_url = "https://browser.example.com/list"
    detail_url = "https://browser.example.com/rendered/article/one"
    rendered_detail = (FIXTURES / "article_en.html").read_bytes()
    raw_detail = b"raw server response before JavaScript rendered the article"
    browser = FakeBrowserPort(
        {
            list_url: RenderedPage(
                url="https://browser.example.com/rendered/",
                body=(
                    b'<main><a class="item" href="article/one">One</a>'
                    b'<a class="item" href="article/two">Two</a></main>'
                ),
                artifact=ResponseArtifact(
                    url=list_url,
                    status_code=200,
                    headers={"content-type": "text/html"},
                    body=b"raw list response",
                ),
            ),
            detail_url: RenderedPage(
                url=detail_url,
                body=rendered_detail,
                artifact=ResponseArtifact(
                    url=detail_url,
                    status_code=200,
                    headers={"content-type": "text/html", "set-cookie": "discard"},
                    body=raw_detail,
                ),
            ),
        }
    )
    definition = source(
        CollectorKind.BROWSER,
        entry_url=list_url,
        config={"link_selector": "main a.item", "item_limit": 1},
    )

    fetch_context = context()
    items = await collected(
        BrowserCollector(enabled=True, browser_port=browser, timeout_seconds=2.5),
        definition,
        fetch_context,
    )

    assert [item.url for item in items] == [detail_url]
    assert items[0].body == rendered_detail
    assert items[0].artifact is not None
    assert items[0].artifact.body == raw_detail
    assert "set-cookie" not in items[0].artifact.headers
    assert browser.requests == [
        BrowserRequest(
            url=list_url,
            allowed_hosts=("browser.example.com",),
            timeout_seconds=2.5,
        ),
        BrowserRequest(
            url=detail_url,
            allowed_hosts=("browser.example.com",),
            timeout_seconds=2.5,
        ),
    ]
    assert fetch_context.metrics == FetchMetrics(
        http_requests=2,
        bytes_received=len(b"raw list response") + len(raw_detail),
    )


@pytest.mark.asyncio
async def test_browser_collector_classifies_disabled_renderer_without_importing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = False

    def unexpected_import(name: str):
        nonlocal imported
        imported = True
        raise AssertionError(name)

    monkeypatch.setattr(importlib, "import_module", unexpected_import)
    definition = source(
        CollectorKind.BROWSER,
        entry_url="https://browser.example.com/list",
        config={"link_selector": "a"},
    )

    with pytest.raises(CollectorError) as error:
        await collected(BrowserCollector(enabled=False), definition)

    assert error.value.code == "renderer_unavailable"
    assert imported is False


@pytest.mark.asyncio
async def test_browser_collector_classifies_missing_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_import(name: str):
        assert name == "playwright.async_api"
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib, "import_module", missing_import)
    definition = source(
        CollectorKind.BROWSER,
        entry_url="https://browser.example.com/list",
        config={"link_selector": "a"},
    )
    resolver = FakeResolver({"browser.example.com": ("1.1.1.1",)})
    browser_port = PlaywrightBrowserPort(safety_policy=UrlSafetyPolicy(resolver))

    with pytest.raises(CollectorError) as error:
        await collected(
            BrowserCollector(enabled=True, browser_port=browser_port),
            definition,
        )

    assert error.value.code == "renderer_unavailable"


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = SimpleNamespace(url=url)
        self.action: str | None = None

    async def abort(self, error_code: str = "blockedbyclient") -> None:
        self.action = f"abort:{error_code}"

    async def continue_(self) -> None:
        self.action = "continue"


class FakeNavigationResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"raw navigation response",
    ) -> None:
        self.status = status
        self._headers = headers or {
            "content-type": "text/html; charset=utf-8",
            "set-cookie": "discard",
        }
        self._body = body

    async def all_headers(self) -> dict[str, str]:
        return dict(self._headers)

    async def body(self) -> bytes:
        return self._body


_DEFAULT_NAVIGATION_RESPONSE = object()


class FakePage:
    def __init__(
        self,
        final_url: str,
        navigation_response: FakeNavigationResponse | None,
    ) -> None:
        self.url = final_url
        self.navigation_response = navigation_response
        self.route_handler: Any = None
        self.default_timeout: float | None = None
        self.navigation_timeout: float | None = None
        self.goto_call: tuple[str, dict[str, Any]] | None = None
        self.content_calls = 0

    def set_default_timeout(self, timeout: float) -> None:
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: float) -> None:
        self.navigation_timeout = timeout

    async def route(self, pattern: str, handler: Any) -> None:
        assert pattern == "**/*"
        self.route_handler = handler

    async def goto(self, url: str, **kwargs: Any) -> FakeNavigationResponse | None:
        self.goto_call = (url, kwargs)
        return self.navigation_response

    async def content(self) -> str:
        self.content_calls += 1
        return '<a href="/rendered">Rendered</a>'


class FakeContext:
    def __init__(
        self,
        final_url: str,
        navigation_response: FakeNavigationResponse | None,
    ) -> None:
        self.page = FakePage(final_url, navigation_response)
        self.closed = False

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(
        self,
        final_url: str,
        navigation_response: FakeNavigationResponse | None,
    ) -> None:
        self.final_url = final_url
        self.navigation_response = navigation_response
        self.contexts: list[FakeContext] = []
        self.context_options: list[dict[str, Any]] = []
        self.launch_options: dict[str, Any] = {}
        self.closed = False

    async def new_context(self, **kwargs: Any) -> FakeContext:
        self.context_options.append(kwargs)
        context = FakeContext(self.final_url, self.navigation_response)
        self.contexts.append(context)
        return context

    async def close(self) -> None:
        self.closed = True


class FakePlaywrightManager:
    def __init__(
        self,
        browsers: list[FakeBrowser],
        final_url: str,
        navigation_response: FakeNavigationResponse | None | object = _DEFAULT_NAVIGATION_RESPONSE,
    ) -> None:
        self.browsers = browsers
        self.final_url = final_url
        self.navigation_response = (
            FakeNavigationResponse()
            if navigation_response is _DEFAULT_NAVIGATION_RESPONSE
            else navigation_response
        )

    async def __aenter__(self):
        assert isinstance(self.navigation_response, FakeNavigationResponse) or (
            self.navigation_response is None
        )
        browser = FakeBrowser(self.final_url, self.navigation_response)
        self.browsers.append(browser)

        async def launch(**kwargs: Any) -> FakeBrowser:
            browser.launch_options = kwargs
            return browser

        return SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_playwright_port_uses_fresh_hardened_nonpersistent_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browsers: list[FakeBrowser] = []
    final_url = "https://xn--bcher-kva.example/final/"
    module = SimpleNamespace(async_playwright=lambda: FakePlaywrightManager(browsers, final_url))
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    resolver = FakeResolver({"xn--bcher-kva.example": ("1.1.1.1",)})
    port = PlaywrightBrowserPort(safety_policy=UrlSafetyPolicy(resolver))
    request = BrowserRequest(
        url="https://bücher.example/list#fragment",
        allowed_hosts=("bücher.example",),
        timeout_seconds=2.5,
    )

    first = await port.render(request)
    second = await port.render(request)

    assert first.body == b'<a href="/rendered">Rendered</a>'
    assert first.artifact.body == b"raw navigation response"
    assert first.artifact.status_code == 200
    assert dict(first.artifact.headers) == {"content-type": "text/html; charset=utf-8"}
    assert second.url == final_url
    assert request.metrics == FetchMetrics(
        http_requests=2,
        bytes_received=len(b"raw navigation response") * 2,
    )
    assert len(browsers) == 2
    for browser in browsers:
        assert browser.launch_options == {
            "headless": True,
            "args": [
                "--disable-quic",
                "--host-resolver-rules=MAP xn--bcher-kva.example 1.1.1.1, MAP * ^NOTFOUND",
            ],
        }
        assert browser.context_options == [{"accept_downloads": False, "service_workers": "block"}]
        assert browser.contexts[0].closed is True
        assert browser.closed is True
        page = browser.contexts[0].page
        assert page.default_timeout == 2500
        assert page.navigation_timeout == 2500
        assert page.goto_call == (
            "https://xn--bcher-kva.example/list",
            {"wait_until": "domcontentloaded", "timeout": 2500},
        )

        blocked = FakeRoute("file:///etc/passwd")
        await page.route_handler(blocked)
        assert blocked.action == "abort:blockedbyclient"

        allowed = FakeRoute("https://xn--bcher-kva.example/script.js")
        await page.route_handler(allowed)
        assert allowed.action == "continue"


@pytest.mark.asyncio
async def test_playwright_port_fails_closed_when_navigation_has_no_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browsers: list[FakeBrowser] = []
    module = SimpleNamespace(
        async_playwright=lambda: FakePlaywrightManager(
            browsers,
            "https://browser.example.com/final/",
            navigation_response=None,
        )
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    resolver = FakeResolver({"browser.example.com": ("1.1.1.1",)})
    port = PlaywrightBrowserPort(safety_policy=UrlSafetyPolicy(resolver))

    request = BrowserRequest(
        url="https://browser.example.com/list",
        allowed_hosts=("browser.example.com",),
        timeout_seconds=2.5,
    )
    with pytest.raises(CollectorError) as error:
        await port.render(request)

    assert error.value.code == "renderer_response_unavailable"
    assert request.metrics == FetchMetrics(http_requests=1)


@pytest.mark.asyncio
async def test_playwright_port_closes_resources_when_raw_response_body_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenBodyResponse(FakeNavigationResponse):
        async def body(self) -> bytes:
            raise RuntimeError("raw response unavailable")

    browsers: list[FakeBrowser] = []
    module = SimpleNamespace(
        async_playwright=lambda: FakePlaywrightManager(
            browsers,
            "https://browser.example.com/final/",
            navigation_response=BrokenBodyResponse(),
        )
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    resolver = FakeResolver({"browser.example.com": ("1.1.1.1",)})
    port = PlaywrightBrowserPort(safety_policy=UrlSafetyPolicy(resolver))

    request = BrowserRequest(
        url="https://browser.example.com/list",
        allowed_hosts=("browser.example.com",),
        timeout_seconds=2.5,
    )
    with pytest.raises(CollectorError) as error:
        await port.render(request)

    assert error.value.code == "renderer_response_unavailable"
    assert request.metrics == FetchMetrics(http_requests=1)
    assert browsers[0].contexts[0].closed is True
    assert browsers[0].closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "addresses"),
    [
        ("http://browser.example.com/list", ("1.1.1.1",)),
        ("https://browser.example.com:8443/list", ("1.1.1.1",)),
        ("https://browser.example.com/list", ("127.0.0.1",)),
    ],
)
async def test_playwright_port_rejects_unsafe_entry_before_browser_launch(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    addresses: tuple[str, ...],
) -> None:
    browsers: list[FakeBrowser] = []
    module = SimpleNamespace(
        async_playwright=lambda: FakePlaywrightManager(
            browsers,
            "https://browser.example.com/final/",
        )
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    resolver = FakeResolver({"browser.example.com": addresses})
    port = PlaywrightBrowserPort(safety_policy=UrlSafetyPolicy(resolver))
    request = BrowserRequest(
        url=url,
        allowed_hosts=("browser.example.com",),
        timeout_seconds=2.5,
    )

    with pytest.raises(CollectorError) as error:
        await port.render(request)

    assert error.value.code == "renderer_security_rejected"
    assert browsers == []
    assert request.metrics == FetchMetrics()


@pytest.mark.asyncio
async def test_playwright_port_records_unavailable_after_initial_safety_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_playwright(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib, "import_module", missing_playwright)
    resolver = FakeResolver({"browser.example.com": ("1.1.1.1",)})
    port = PlaywrightBrowserPort(safety_policy=UrlSafetyPolicy(resolver))
    request = BrowserRequest(
        url="https://browser.example.com/list",
        allowed_hosts=("browser.example.com",),
        timeout_seconds=2.5,
    )

    with pytest.raises(CollectorError) as error:
        await port.render(request)

    assert error.value.code == "renderer_unavailable"
    assert request.metrics == FetchMetrics(http_requests=1)


@pytest.mark.asyncio
async def test_playwright_port_revalidates_and_blocks_unsafe_subresources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browsers: list[FakeBrowser] = []
    module = SimpleNamespace(
        async_playwright=lambda: FakePlaywrightManager(
            browsers,
            "https://browser.example.com/final/",
        )
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    resolver = FakeResolver(
        {
            "browser.example.com": ("1.1.1.1",),
            "cdn.example.com": ("8.8.8.8",),
        }
    )
    port = PlaywrightBrowserPort(safety_policy=UrlSafetyPolicy(resolver))
    request = BrowserRequest(
        url="https://browser.example.com/list",
        allowed_hosts=("browser.example.com", "cdn.example.com"),
        timeout_seconds=2.5,
    )

    await port.render(request)
    resolver.addresses["cdn.example.com"] = ("127.0.0.1",)
    route = FakeRoute("https://cdn.example.com/script.js")
    await browsers[0].contexts[0].page.route_handler(route)

    assert route.action == "abort:blockedbyclient"


@pytest.mark.asyncio
async def test_playwright_port_rejects_unsafe_final_redirect_before_reading_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browsers: list[FakeBrowser] = []
    module = SimpleNamespace(
        async_playwright=lambda: FakePlaywrightManager(
            browsers,
            "https://redirected.example.com/private",
        )
    )
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    resolver = FakeResolver({"browser.example.com": ("1.1.1.1",)})
    port = PlaywrightBrowserPort(safety_policy=UrlSafetyPolicy(resolver))
    request = BrowserRequest(
        url="https://browser.example.com/list",
        allowed_hosts=("browser.example.com",),
        timeout_seconds=2.5,
    )

    with pytest.raises(CollectorError) as error:
        await port.render(request)

    assert error.value.code == "renderer_security_rejected"
    assert browsers[0].contexts[0].page.content_calls == 0
