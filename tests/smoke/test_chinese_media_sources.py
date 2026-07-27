from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from commerce_agent.ingestion.bootstrap import build_resolver_bundle
from commerce_agent.ingestion.collectors import HtmlCollector
from commerce_agent.ingestion.collectors.base import HttpPort
from commerce_agent.ingestion.extract import ContentExtractor, LinguaLanguageDetector
from commerce_agent.ingestion.http import (
    FetchError,
    FetchRequest,
    FetchResponse,
    IngestionHttpClient,
)
from commerce_agent.ingestion.models import CollectedFailure, CollectedItem, FetchContext, Trigger
from commerce_agent.ingestion.registry import SourceRegistry
from commerce_agent.ingestion.security import UrlSafetyError

_REGISTRY_PATH = (
    Path(__file__).parents[2] / "src" / "commerce_agent" / "sources" / "public_sources.yaml"
)
_SMOKE_SOURCES = (
    ("media-cifnews-cross-border", "cifnews.com"),
    ("media-100ec-cross-border", "100ec.cn"),
)


@pytest.mark.skipif(
    os.getenv("RUN_CHINESE_MEDIA_SMOKE") != "1",
    reason="set RUN_CHINESE_MEDIA_SMOKE=1 to run controlled Chinese-media checks",
)
@pytest.mark.parametrize(("source_id", "expected_publisher"), _SMOKE_SOURCES)
async def test_direct_chinese_media_yields_one_bounded_full_text_document(
    source_id: str,
    expected_publisher: str,
) -> None:
    registry_source = SourceRegistry.from_yaml(_REGISTRY_PATH).require(source_id)
    original_item_limit = registry_source.collector_config["item_limit"]
    source = replace(
        registry_source,
        collector_config={**registry_source.collector_config, "item_limit": 1},
    )
    context = FetchContext(trigger=Trigger.MANUAL, started_at=datetime.now(UTC))
    resolver_bundle = build_resolver_bundle("cloudflare_doh")
    client = IngestionHttpClient(
        safety_policy=resolver_bundle.safety_policy,
        global_concurrency=1,
        domain_rps=0.5,
        timeout_seconds=20,
        max_retries=0,
        max_redirects=0,
        max_response_bytes=2_000_000,
    )
    budgeted_client = _TwoRequestBudget(client)

    try:
        try:
            results = [
                result
                async for result in HtmlCollector(budgeted_client).collect(source, context)
            ]
        except (FetchError, UrlSafetyError) as error:
            pytest.fail(
                _fetch_failure_diagnostic(source_id, budgeted_client, error),
                pytrace=False,
            )
    finally:
        await client.aclose()
        for resource in resolver_bundle.resources:
            await resource.aclose()

    items = [result for result in results if isinstance(result, CollectedItem)]
    assert len(items) == 1, _result_diagnostic(source_id, budgeted_client, results)
    item = items[0]
    assert urlsplit(item.url).scheme == "https"
    assert item.body
    assert budgeted_client.request_count <= 2
    assert context.metrics.http_requests <= 2
    assert registry_source.collector_config["item_limit"] == original_item_limit

    document = ContentExtractor(LinguaLanguageDetector()).extract(
        source,
        item,
        fetched_at=datetime.now(UTC),
    )

    assert document.body
    assert document.metadata["content_scope"] == "full_text"
    assert document.metadata["publisher_key"] == expected_publisher


class _TwoRequestBudget:
    def __init__(self, client: HttpPort) -> None:
        self._client = client
        self.requests: list[FetchRequest] = []
        self.responses: list[FetchResponse] = []

    @property
    def request_count(self) -> int:
        return len(self.requests)

    async def get(self, request: FetchRequest) -> FetchResponse:
        self.requests.append(request)
        if self.request_count > 2:
            raise AssertionError("Chinese-media smoke exceeded its two-request budget")
        response = await self._client.get(request)
        self.responses.append(response)
        return response


def _request_summary(client: _TwoRequestBudget) -> str:
    statuses = [response.status_code for response in client.responses]
    urls = [request.url for request in client.requests]
    return f"requests={client.request_count}; urls={urls!r}; statuses={statuses!r}"


def _fetch_failure_diagnostic(
    source_id: str,
    client: _TwoRequestBudget,
    error: FetchError | UrlSafetyError,
) -> str:
    code = getattr(error, "code", type(error).__name__)
    status = getattr(error, "status_code", None)
    return (
        f"{source_id}: {_request_summary(client)}; "
        f"stable_failure_code={code}; failure_status={status!r}"
    )


def _result_diagnostic(
    source_id: str,
    client: _TwoRequestBudget,
    results: list[CollectedItem | CollectedFailure],
) -> str:
    failure_codes = [
        result.error_code for result in results if isinstance(result, CollectedFailure)
    ]
    item_urls = [result.url for result in results if isinstance(result, CollectedItem)]
    return (
        f"{source_id}: {_request_summary(client)}; "
        f"candidate_documents={item_urls!r}; failure_codes={failure_codes!r}"
    )
