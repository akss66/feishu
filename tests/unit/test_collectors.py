from __future__ import annotations

import ast
import importlib
import json
import sys
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
from commerce_agent.ingestion.http import FetchRequest, FetchResponse
from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    FetchContext,
    Platform,
    SourceDefinition,
    Trigger,
    TrustTier,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ingestion"


class FakeHttpPort:
    def __init__(self, responses: dict[str, bytes | FetchResponse]) -> None:
        self.responses = responses
        self.requests: list[FetchRequest] = []

    async def get(self, request: FetchRequest) -> FetchResponse:
        self.requests.append(request)
        response = self.responses[request.url]
        if isinstance(response, FetchResponse):
            return response
        return FetchResponse(
            url=request.url,
            status_code=200,
            headers={"content-type": "application/octet-stream"},
            body=response,
        )


class FakeBrowserPort:
    def __init__(self, page: RenderedPage) -> None:
        self.page = page
        self.requests: list[BrowserRequest] = []

    async def render(self, request: BrowserRequest) -> RenderedPage:
        self.requests.append(request)
        return self.page


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


async def collected(collector: Collector, definition: SourceDefinition):
    return [item async for item in collector.collect(definition, context())]


def bundled_xml(name: str) -> dict[str, bytes]:
    root = ElementTree.fromstring((FIXTURES / name).read_bytes())
    return {
        node.attrib.get("name", node.attrib.get("url", "")): (node.text or "").strip().encode()
        for node in root.findall("document")
    }


def test_importing_collectors_does_not_import_playwright() -> None:
    assert "playwright" not in sys.modules
    assert "playwright.async_api" not in sys.modules


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
async def test_sitemap_collector_walks_nested_indexes_and_namespaced_urlsets() -> None:
    documents = bundled_xml("sitemap.xml")
    http = FakeHttpPort(documents)
    definition = source(
        CollectorKind.SITEMAP,
        entry_url="https://docs.example.com/sitemap.xml",
        config={"item_limit": 10},
    )

    items = await collected(SitemapCollector(http), definition)

    assert [item.url for item in items] == [
        "https://docs.example.com/articles/alpha",
        "https://docs.example.com/articles/shared",
        "https://docs.example.com/articles/bravo",
    ]
    assert [request.url for request in http.requests] == [
        "https://docs.example.com/sitemap.xml",
        "https://docs.example.com/sitemaps/nested.xml",
        "https://docs.example.com/sitemaps/articles-a.xml",
        "https://docs.example.com/sitemaps/articles-b.xml",
    ]
    assert all(request.allowed_hosts == ("docs.example.com",) for request in http.requests)
    assert http.requests[0].etag == '"previous"'
    assert all(request.etag is None for request in http.requests[1:])


@pytest.mark.asyncio
async def test_sitemap_collector_caps_unique_candidates() -> None:
    documents = bundled_xml("sitemap.xml")
    definition = source(
        CollectorKind.SITEMAP,
        entry_url="https://docs.example.com/sitemap.xml",
        config={"item_limit": 2},
    )

    items = await collected(SitemapCollector(FakeHttpPort(documents)), definition)

    assert [item.url for item in items] == [
        "https://docs.example.com/articles/alpha",
        "https://docs.example.com/articles/shared",
    ]


@pytest.mark.asyncio
async def test_html_collector_uses_selector_resolves_links_and_deduplicates() -> None:
    url = "https://news.example.com/list/index.html"
    response = FetchResponse(
        url="https://news.example.com/updates/",
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=(FIXTURES / "list.html").read_bytes(),
    )
    http = FakeHttpPort({url: response})
    definition = source(
        CollectorKind.HTML,
        entry_url=url,
        config={"link_selector": "main#updates article a.update-link", "item_limit": 2},
    )

    items = await collected(HtmlCollector(http), definition)

    assert [item.url for item in items] == [
        "https://news.example.com/articles/alpha",
        "https://news.example.com/updates/articles/bravo",
    ]
    assert [item.title for item in items] == ["Alpha", "Bravo"]
    assert len(http.requests) == 1


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

    items = await collected(ApiCollector(http), definition)

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
    browser = FakeBrowserPort(
        RenderedPage(
            url="https://browser.example.com/rendered/",
            body=b'<main><a class="item" href="article/one">One</a></main>',
        )
    )
    definition = source(
        CollectorKind.BROWSER,
        entry_url="https://browser.example.com/list",
        config={"link_selector": "main a.item", "item_limit": 5},
    )

    items = await collected(
        BrowserCollector(enabled=True, browser_port=browser, timeout_seconds=2.5),
        definition,
    )

    assert [item.url for item in items] == ["https://browser.example.com/rendered/article/one"]
    assert browser.requests == [
        BrowserRequest(
            url="https://browser.example.com/list",
            allowed_hosts=("browser.example.com",),
            timeout_seconds=2.5,
        )
    ]


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

    with pytest.raises(CollectorError) as error:
        await collected(BrowserCollector(enabled=True), definition)

    assert error.value.code == "renderer_unavailable"


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = SimpleNamespace(url=url)
        self.action: str | None = None

    async def abort(self, error_code: str = "blockedbyclient") -> None:
        self.action = f"abort:{error_code}"

    async def continue_(self) -> None:
        self.action = "continue"


class FakePage:
    def __init__(self) -> None:
        self.url = "https://browser.example.com/final/"
        self.route_handler: Any = None
        self.default_timeout: float | None = None
        self.navigation_timeout: float | None = None
        self.goto_call: tuple[str, dict[str, Any]] | None = None

    def set_default_timeout(self, timeout: float) -> None:
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: float) -> None:
        self.navigation_timeout = timeout

    async def route(self, pattern: str, handler: Any) -> None:
        assert pattern == "**/*"
        self.route_handler = handler

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_call = (url, kwargs)

    async def content(self) -> str:
        return '<a href="/rendered">Rendered</a>'


class FakeContext:
    def __init__(self) -> None:
        self.page = FakePage()
        self.closed = False

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []
        self.context_options: list[dict[str, Any]] = []
        self.closed = False

    async def new_context(self, **kwargs: Any) -> FakeContext:
        self.context_options.append(kwargs)
        context = FakeContext()
        self.contexts.append(context)
        return context

    async def close(self) -> None:
        self.closed = True


class FakePlaywrightManager:
    def __init__(self, browsers: list[FakeBrowser]) -> None:
        self.browsers = browsers

    async def __aenter__(self):
        browser = FakeBrowser()
        self.browsers.append(browser)

        async def launch(**kwargs: Any) -> FakeBrowser:
            assert kwargs == {"headless": True}
            return browser

        return SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_playwright_port_uses_fresh_hardened_nonpersistent_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browsers: list[FakeBrowser] = []
    module = SimpleNamespace(async_playwright=lambda: FakePlaywrightManager(browsers))
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    port = PlaywrightBrowserPort()
    request = BrowserRequest(
        url="https://browser.example.com/list",
        allowed_hosts=("browser.example.com",),
        timeout_seconds=2.5,
    )

    first = await port.render(request)
    second = await port.render(request)

    assert first.body == b'<a href="/rendered">Rendered</a>'
    assert second.url == "https://browser.example.com/final/"
    assert len(browsers) == 2
    for browser in browsers:
        assert browser.context_options == [
            {"accept_downloads": False, "service_workers": "block"}
        ]
        assert browser.contexts[0].closed is True
        assert browser.closed is True
        page = browser.contexts[0].page
        assert page.default_timeout == 2500
        assert page.navigation_timeout == 2500
        assert page.goto_call == (
            "https://browser.example.com/list",
            {"wait_until": "domcontentloaded", "timeout": 2500},
        )

        blocked = FakeRoute("file:///etc/passwd")
        await page.route_handler(blocked)
        assert blocked.action == "abort:blockedbyclient"

        allowed = FakeRoute("https://browser.example.com/script.js")
        await page.route_handler(allowed)
        assert allowed.action == "continue"
