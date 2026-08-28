from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from commerce_agent.ingestion.collectors import HtmlCollector
from commerce_agent.ingestion.collectors.base import allowed_hosts
from commerce_agent.ingestion.collectors.html import links_from_html
from commerce_agent.ingestion.http import FetchRequest, FetchResponse
from commerce_agent.ingestion.models import (
    CollectedFailure,
    CollectedItem,
    CollectorKind,
    ComplianceStatus,
    ContentScope,
    FetchContext,
    Platform,
    SourceDefinition,
    Trigger,
    TrustTier,
)
from commerce_agent.ingestion.registry import SourceRegistry, SourceRegistryError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ingestion"


class FakeHttpPort:
    def __init__(self, responses: dict[str, FetchResponse]) -> None:
        self.responses = responses
        self.requests: list[FetchRequest] = []

    async def get(self, request: FetchRequest) -> FetchResponse:
        self.requests.append(request)
        return self.responses[request.url]


def fixture_response(name: str, url: str) -> FetchResponse:
    return FetchResponse(
        url=url,
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
        body=(FIXTURES / name).read_bytes(),
    )


def direct_media_source(
    *,
    source_id: str,
    entry_url: str,
    platforms: tuple[Platform, ...],
    config: dict[str, str | int | bool],
) -> SourceDefinition:
    return SourceDefinition(
        source_id=source_id,
        name=f"Fixture {source_id}",
        entry_url=entry_url,
        platforms=platforms,
        trust_tier=TrustTier.MEDIA,
        collector=CollectorKind.HTML,
        compliance=ComplianceStatus.ALLOWED,
        enabled=True,
        regions=("global",),
        language_hint="zh",
        interval_minutes=120,
        terms_url=f"{entry_url.rstrip('/')}/terms",
        robots_url=f"{entry_url.rstrip('/')}/robots.txt",
        reviewed_at=date(2026, 7, 27),
        compliance_notes="Offline fixture for a reviewed direct industry media source.",
        content_scope=ContentScope.FULL_TEXT,
        attribution=f"Source: {source_id}",
        publisher_key=source_id.removeprefix("media-").removesuffix("-cross-border"),
        collector_config=config,
    )


def registry_document(
    collector_config: dict[str, str | int | bool],
    *,
    entry_url: str = "https://www.cifnews.com/",
) -> dict[str, object]:
    return {
        "version": 1,
        "sources": [
            {
                "source_id": "media-cifnews-cross-border",
                "name": "雨果跨境",
                "entry_url": entry_url,
                "platforms": ["amazon"],
                "trust_tier": "media",
                "collector": "html",
                "compliance": "allowed",
                "enabled": True,
                "regions": ["global"],
                "language_hint": "zh",
                "interval_minutes": 120,
                "terms_url": "https://www.cifnews.com/terms",
                "robots_url": "https://www.cifnews.com/robots.txt",
                "reviewed_at": "2026-07-27",
                "compliance_notes": "Reviewed direct industry media fixture.",
                "adapter": "generic",
                "content_scope": "full_text",
                "attribution": "来源：雨果跨境",
                "publisher_key": "cifnews.com",
                "collector_config": collector_config,
            }
        ],
    }


def load_registry(
    tmp_path: Path,
    collector_config: dict[str, str | int | bool],
    *,
    entry_url: str = "https://www.cifnews.com/",
) -> SourceRegistry:
    path = tmp_path / "sources.yaml"
    path.write_text(
        yaml.safe_dump(
            registry_document(collector_config, entry_url=entry_url),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return SourceRegistry.from_yaml(path)


def context() -> FetchContext:
    return FetchContext(
        trigger=Trigger.MANUAL,
        started_at=datetime(2026, 7, 27, tzinfo=UTC),
    )


async def collect_results(
    collector: HtmlCollector,
    source: SourceDefinition,
) -> list[CollectedItem | CollectedFailure]:
    return [result async for result in collector.collect(source, context())]


async def collect_items(
    collector: HtmlCollector,
    source: SourceDefinition,
) -> list[CollectedItem]:
    return [
        result
        for result in await collect_results(collector, source)
        if isinstance(result, CollectedItem)
    ]


@pytest.mark.asyncio
async def test_cifnews_fetches_only_scoped_platform_article() -> None:
    source = direct_media_source(
        source_id="media-cifnews-cross-border",
        entry_url="https://www.cifnews.com/",
        platforms=(Platform.AMAZON,),
        config={
            "link_selector": "a",
            "link_path_prefixes": "/article/",
            "allowed_hosts": "www.cifnews.com,static.cifnews.com",
            "public_article_gate": True,
            "item_limit": 10,
        },
    )
    http = FakeHttpPort(
        {
            source.entry_url: fixture_response("cifnews_home.html", source.entry_url),
            "https://www.cifnews.com/article/187800": fixture_response(
                "cifnews_article.html",
                "https://www.cifnews.com/article/187800",
            ),
        }
    )

    items = await collect_items(HtmlCollector(http), source)

    assert [request.url for request in http.requests] == [
        source.entry_url,
        "https://www.cifnews.com/article/187800",
    ]
    assert all(
        request.allowed_hosts == ("www.cifnews.com", "static.cifnews.com")
        for request in http.requests
    )
    assert len(items) == 1


@pytest.mark.asyncio
async def test_html_collector_matches_unicode_candidate_to_punycode_allowlist() -> None:
    source = direct_media_source(
        source_id="media-idna-cross-border",
        entry_url="https://xn--fa-hia.de/",
        platforms=(Platform.AMAZON,),
        config={
            "link_selector": "a.idna",
            "link_path_prefixes": "/article/",
            "allowed_hosts": "xn--fa-hia.de",
            "item_limit": 1,
        },
    )
    detail_url = "https://faß.de/article/1"
    http = FakeHttpPort(
        {
            source.entry_url: fixture_response("cifnews_home.html", source.entry_url),
            detail_url: fixture_response("cifnews_article.html", detail_url),
        }
    )

    items = await collect_items(HtmlCollector(http), source)

    assert [request.url for request in http.requests] == [
        source.entry_url,
        detail_url,
    ]
    assert all(request.allowed_hosts == ("xn--fa-hia.de",) for request in http.requests)
    assert [item.url for item in items] == [detail_url]


@pytest.mark.asyncio
async def test_100ec_challenge_is_not_emitted_as_full_text() -> None:
    source = direct_media_source(
        source_id="media-100ec-cross-border",
        entry_url="https://imgs-b2b.100ec.cn/list--3--1.html",
        platforms=(Platform.TEMU,),
        config={
            "link_selector": "a",
            "link_path_prefixes": "/detail--",
            "allowed_hosts": "imgs-b2b.100ec.cn",
            "public_article_gate": True,
            "item_limit": 5,
        },
    )
    http = FakeHttpPort(
        {
            source.entry_url: fixture_response("100ec_list.html", source.entry_url),
            "https://imgs-b2b.100ec.cn/detail--6659472.html": fixture_response(
                "100ec_challenge.html",
                "https://imgs-b2b.100ec.cn/detail--6659472.html",
            ),
        }
    )

    results = await collect_results(HtmlCollector(http), source)

    assert not any(isinstance(result, CollectedItem) for result in results)
    assert [result.error_code for result in results if isinstance(result, CollectedFailure)] == [
        "article_access_wall"
    ]


@pytest.mark.asyncio
async def test_100ec_emits_complete_platform_article() -> None:
    source = direct_media_source(
        source_id="media-100ec-cross-border",
        entry_url="https://imgs-b2b.100ec.cn/list--3--1.html",
        platforms=(Platform.TEMU,),
        config={
            "link_selector": "a",
            "link_path_prefixes": "/detail--",
            "allowed_hosts": "imgs-b2b.100ec.cn",
            "public_article_gate": True,
            "item_limit": 5,
        },
    )
    article_url = "https://imgs-b2b.100ec.cn/detail--6659472.html"
    http = FakeHttpPort(
        {
            source.entry_url: fixture_response("100ec_list.html", source.entry_url),
            article_url: fixture_response("100ec_article.html", article_url),
        }
    )

    items = await collect_items(HtmlCollector(http), source)

    assert [request.url for request in http.requests] == [source.entry_url, article_url]
    assert [item.url for item in items] == [article_url]


@pytest.mark.asyncio
async def test_100ec_rejects_matching_candidate_on_unconfigured_host_before_request() -> None:
    source = direct_media_source(
        source_id="media-100ec-cross-border",
        entry_url="https://imgs-b2b.100ec.cn/list--3--1.html",
        platforms=(Platform.TEMU,),
        config={
            "link_selector": "a.external",
            "link_path_prefixes": "/detail--",
            "allowed_hosts": "imgs-b2b.100ec.cn",
            "public_article_gate": True,
            "item_limit": 5,
        },
    )
    http = FakeHttpPort(
        {
            source.entry_url: fixture_response("100ec_list.html", source.entry_url),
        }
    )

    results = await collect_results(HtmlCollector(http), source)

    assert results == []
    assert [request.url for request in http.requests] == [source.entry_url]


def test_rejected_candidates_do_not_consume_html_item_limit() -> None:
    items = links_from_html(
        (FIXTURES / "cifnews_home.html").read_bytes(),
        base_url="https://www.cifnews.com/",
        selector="a",
        limit=1,
        candidate_filter=lambda candidate: candidate.url.endswith("/article/187801"),
    )

    assert [item.url for item in items] == ["https://www.cifnews.com/article/187801"]


def test_allowed_hosts_normalizes_configured_allowlist() -> None:
    source = direct_media_source(
        source_id="media-cifnews-cross-border",
        entry_url="https://www.cifnews.com/",
        platforms=(Platform.AMAZON,),
        config={
            "link_selector": "a",
            "allowed_hosts": " WWW.CIFNEWS.COM.,static.cifnews.com,www.cifnews.com ",
        },
    )

    assert allowed_hosts(source) == (
        "www.cifnews.com",
        "static.cifnews.com",
    )


def test_registry_accepts_direct_html_scope_and_gate_config(tmp_path: Path) -> None:
    registry = load_registry(
        tmp_path,
        {
            "link_selector": "a",
            "article_selector": "article",
            "item_limit": 10,
            "allowed_hosts": "www.cifnews.com,static.cifnews.com",
            "link_path_prefixes": "/article/,/news/",
            "public_article_gate": True,
        },
    )

    assert registry.require("media-cifnews-cross-border").collector_config == {
        "link_selector": "a",
        "article_selector": "article",
        "item_limit": 10,
        "allowed_hosts": "www.cifnews.com,static.cifnews.com",
        "link_path_prefixes": "/article/,/news/",
        "public_article_gate": True,
    }


def test_registry_matches_unicode_entry_host_to_punycode_allowlist(
    tmp_path: Path,
) -> None:
    registry = load_registry(
        tmp_path,
        {
            "link_selector": "a",
            "allowed_hosts": "xn--fa-hia.de",
        },
        entry_url="https://faß.de/",
    )

    assert registry.require("media-cifnews-cross-border").collector_config[
        "allowed_hosts"
    ] == "xn--fa-hia.de"


@pytest.mark.parametrize(
    "allowed_hosts_value",
    [
        "https://www.cifnews.com",
        "www.cifnews.com:443",
        "www.cifnews.com/article",
        "*.cifnews.com",
        "WWW.CIFNEWS.COM",
        "localhost",
        "127.0.0.1",
        "169.254.169.254",
        "metadata.google.internal",
        "static.cifnews.com",
        "www.cifnews.com,",
    ],
)
def test_registry_rejects_unsafe_or_unscoped_allowed_hosts(
    tmp_path: Path,
    allowed_hosts_value: str,
) -> None:
    with pytest.raises(SourceRegistryError, match="allowed_hosts"):
        load_registry(
            tmp_path,
            {
                "link_selector": "a",
                "allowed_hosts": allowed_hosts_value,
            },
        )


@pytest.mark.parametrize(
    "path_prefixes",
    [
        "article/",
        "/",
        "/article/?draft=1",
        "/article/#top",
        "//evil.example",
        "/article/,",
    ],
)
def test_registry_rejects_invalid_link_path_prefixes(
    tmp_path: Path,
    path_prefixes: str,
) -> None:
    with pytest.raises(SourceRegistryError, match="link_path_prefixes"):
        load_registry(
            tmp_path,
            {
                "link_selector": "a",
                "link_path_prefixes": path_prefixes,
            },
        )


def test_registry_rejects_non_boolean_public_article_gate(tmp_path: Path) -> None:
    with pytest.raises(SourceRegistryError, match="public_article_gate.*boolean"):
        load_registry(
            tmp_path,
            {
                "link_selector": "a",
                "public_article_gate": "true",
            },
        )


def test_registry_error_does_not_echo_untrusted_host_value(tmp_path: Path) -> None:
    marker = "private-page-token-987"

    with pytest.raises(SourceRegistryError) as error:
        load_registry(
            tmp_path,
            {
                "link_selector": "a",
                "allowed_hosts": f"www.cifnews.com/{marker}",
            },
        )

    assert marker not in str(error.value)
