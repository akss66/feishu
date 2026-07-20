from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from commerce_agent.ingestion.collectors import FeedCollector
from commerce_agent.ingestion.collectors.base import HttpPort
from commerce_agent.ingestion.http import FetchRequest, FetchResponse, IngestionHttpClient
from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    FetchContext,
    FetchMetrics,
    SourceDefinition,
    Trigger,
    TrustTier,
)
from commerce_agent.ingestion.registry import SourceRegistry
from commerce_agent.ingestion.security import UrlSafetyPolicy

_REGISTRY_PATH = (
    Path(__file__).parents[2] / "src" / "commerce_agent" / "sources" / "public_sources.yaml"
)
_SMOKE_SOURCE_IDS = ("ebay-newsroom-rss",)
_FEED_CONTENT_TYPES = frozenset(
    {
        "application/atom+xml",
        "application/rss+xml",
        "application/xml",
        "text/xml",
    }
)


def test_smoke_selection_is_small_reviewed_and_official() -> None:
    sources = _selected_smoke_sources()

    assert tuple(source.source_id for source in sources) == ("ebay-newsroom-rss",)


async def test_feed_smoke_budget_allows_one_list_and_no_detail_request() -> None:
    source = _selected_smoke_sources()[0]
    client = _SingleRequestBudget(_StaticFeedClient(source.entry_url))
    context = FetchContext(trigger=Trigger.MANUAL, started_at=datetime.now(UTC))

    candidates = [candidate async for candidate in FeedCollector(client).collect(source, context)]

    assert client.requests == 1
    assert len(candidates) == 1
    assert candidates[0].url == "https://www.ebayinc.com/stories/news/example/"


@pytest.mark.skipif(
    os.getenv("RUN_PUBLIC_SOURCE_SMOKE") != "1",
    reason="set RUN_PUBLIC_SOURCE_SMOKE=1 to run controlled public-source checks",
)
async def test_reviewed_official_sources_are_reachable_and_yield_candidates() -> None:
    sources = _selected_smoke_sources()

    assert tuple(source.source_id for source in sources) == ("ebay-newsroom-rss",)
    for source in sources:
        metrics = FetchMetrics()
        context = FetchContext(
            trigger=Trigger.MANUAL,
            started_at=datetime.now(UTC),
            metrics=metrics,
        )
        client = IngestionHttpClient(
            safety_policy=UrlSafetyPolicy(),
            global_concurrency=1,
            domain_rps=0.5,
            timeout_seconds=20,
            max_response_bytes=2_000_000,
            max_retries=0,
            max_redirects=0,
        )
        budgeted_client = _SingleRequestBudget(client)
        try:
            candidates = [
                candidate
                async for candidate in FeedCollector(budgeted_client).collect(source, context)
            ]
        finally:
            await client.aclose()

        assert budgeted_client.requests == 1
        assert metrics.http_requests == 1
        assert candidates
        artifact = candidates[0].artifact
        assert artifact is not None
        assert artifact.status_code == 200
        content_type = artifact.headers.get("content-type", "").split(";", 1)[0].lower()
        assert content_type in _FEED_CONTENT_TYPES
        assert all(urlsplit(candidate.url).scheme == "https" for candidate in candidates)


def _selected_smoke_sources() -> tuple[SourceDefinition, ...]:
    registry = SourceRegistry.from_yaml(_REGISTRY_PATH)
    sources = tuple(registry.require(source_id) for source_id in _SMOKE_SOURCE_IDS)
    assert len(sources) <= 2
    assert all(source.trust_tier is TrustTier.OFFICIAL for source in sources)
    assert all(source.compliance is ComplianceStatus.ALLOWED for source in sources)
    assert all(source.enabled for source in sources)
    assert all(source.collector is CollectorKind.RSS for source in sources)
    return sources


class _SingleRequestBudget:
    def __init__(self, client: HttpPort) -> None:
        self._client = client
        self.requests = 0

    async def get(self, request: FetchRequest) -> FetchResponse:
        self.requests += 1
        if self.requests > 1:
            raise AssertionError("public-source smoke exceeded its one-request budget")
        return await self._client.get(request)


class _StaticFeedClient:
    def __init__(self, response_url: str) -> None:
        self._response_url = response_url

    async def get(self, request: FetchRequest) -> FetchResponse:
        del request
        return FetchResponse(
            url=self._response_url,
            status_code=200,
            headers={"content-type": "application/rss+xml"},
            body=b"""<?xml version="1.0"?>
                <rss version="2.0"><channel><item>
                <title>Example</title>
                <link>https://www.ebayinc.com/stories/news/example/</link>
                </item></channel></rss>""",
        )
