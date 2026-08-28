from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
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
from commerce_agent.ingestion.models import (
    CollectedFailure,
    CollectedItem,
    FetchContext,
    Platform,
    Trigger,
)
from commerce_agent.ingestion.registry import SourceRegistry
from commerce_agent.ingestion.security import UrlSafetyError, UrlSafetyPolicy

_REGISTRY_PATH = (
    Path(__file__).parents[2] / "src" / "commerce_agent" / "sources" / "public_sources.yaml"
)
_SMOKE_SOURCES = (
    ("media-cifnews-cross-border", "cifnews.com"),
    ("media-100ec-cross-border", "100ec.cn"),
)
_EVIDENCE_DIR = (
    Path(__file__).parents[2]
    / ".superpowers"
    / "sdd"
    / "2026-07-27-chinese-industry-media-direct-ingestion"
    / "live-smoke-evidence"
)


@dataclass(frozen=True, slots=True)
class SmokeEvidence:
    source_id: str
    executed_at: str
    response_statuses: tuple[int, ...]
    request_count: int
    accepted_candidate_count: int
    extracted_count: int
    gate_result: str
    matched_platforms: tuple[str, ...]


def test_request_summary_redacts_query_values_without_losing_host_and_path() -> None:
    client = _TwoRequestBudget(_NeverCalledHttpPort())
    client.requests.append(
        FetchRequest(
            url=("https://news.example.com/path/to/article?token=private-marker&safe=value"),
            allowed_hosts=("news.example.com",),
        )
    )

    summary = _request_summary(client)

    assert "https://news.example.com/path/to/article" in summary
    assert "private-marker" not in summary
    assert "safe=value" not in summary


def test_result_diagnostic_redacts_item_url_and_preserves_failure_codes() -> None:
    client = _TwoRequestBudget(_NeverCalledHttpPort())
    results: list[CollectedItem | CollectedFailure] = [
        CollectedItem(
            url=(
                "https://news.example.com/path/to/article"
                "?token=private-marker&safe=value#private-fragment"
            ),
            body=b"article",
        ),
        CollectedFailure("article_access_wall"),
    ]

    diagnostic = _result_diagnostic("fixture-source", client, results)

    assert "https://news.example.com/path/to/article" in diagnostic
    assert "private-marker" not in diagnostic
    assert "safe=value" not in diagnostic
    assert "private-fragment" not in diagnostic
    assert "article_access_wall" in diagnostic


def test_structured_smoke_evidence_is_exact_and_secret_free() -> None:
    client = _TwoRequestBudget(_NeverCalledHttpPort())
    client.requests.extend(
        (
            FetchRequest(
                "https://news.example.com/list?token=secret",
                ("news.example.com",),
            ),
            FetchRequest(
                "https://news.example.com/article#secret",
                ("news.example.com",),
            ),
        )
    )
    client.responses.extend(
        (
            FetchResponse("https://news.example.com/list", 200),
            FetchResponse("https://news.example.com/article", 206),
        )
    )
    results: list[CollectedItem | CollectedFailure] = [
        CollectedItem(
            "https://news.example.com/article?token=secret",
            b"article",
            platforms=(Platform.AMAZON,),
        )
    ]

    evidence = _structured_evidence(
        "fixture-source",
        datetime(2026, 7, 27, 12, 34, 56, tzinfo=UTC),
        client,
        results,
        extracted_count=1,
        matched_platforms=(Platform.AMAZON,),
    )
    encoded = json.dumps(asdict(evidence), ensure_ascii=False)

    assert evidence.response_statuses == (200, 206)
    assert evidence.request_count == 2
    assert evidence.accepted_candidate_count == 1
    assert evidence.extracted_count == 1
    assert evidence.gate_result == "accepted"
    assert evidence.matched_platforms == ("amazon",)
    assert "secret" not in encoded


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
                result async for result in HtmlCollector(budgeted_client).collect(source, context)
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
    evidence = _structured_evidence(
        source_id,
        context.started_at,
        budgeted_client,
        results,
        extracted_count=1,
        matched_platforms=document.platforms,
    )
    _write_structured_evidence(evidence)


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


class _NeverCalledHttpPort:
    async def get(self, request: FetchRequest) -> FetchResponse:
        del request
        raise AssertionError("redaction regression must not perform a network request")


def _request_summary(client: _TwoRequestBudget) -> str:
    statuses = [response.status_code for response in client.responses]
    urls = _redacted_urls(request.url for request in client.requests)
    return f"requests={client.request_count}; urls={urls!r}; statuses={statuses!r}"


def _redacted_urls(urls: Iterable[object]) -> list[str]:
    redactor = UrlSafetyPolicy()
    return [redactor.redact_for_log(url) for url in urls]


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
    item_urls = _redacted_urls(
        result.url for result in results if isinstance(result, CollectedItem)
    )
    return (
        f"{source_id}: {_request_summary(client)}; "
        f"candidate_documents={item_urls!r}; failure_codes={failure_codes!r}"
    )


def _structured_evidence(
    source_id: str,
    executed_at: datetime,
    client: _TwoRequestBudget,
    results: list[CollectedItem | CollectedFailure],
    *,
    extracted_count: int,
    matched_platforms: tuple[Platform, ...],
) -> SmokeEvidence:
    accepted = sum(isinstance(result, CollectedItem) for result in results)
    return SmokeEvidence(
        source_id=source_id,
        executed_at=executed_at.isoformat(),
        response_statuses=tuple(response.status_code for response in client.responses),
        request_count=client.request_count,
        accepted_candidate_count=accepted,
        extracted_count=extracted_count,
        gate_result="accepted" if accepted and extracted_count else "rejected",
        matched_platforms=tuple(platform.value for platform in matched_platforms),
    )


def _write_structured_evidence(evidence: SmokeEvidence) -> None:
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    target = _EVIDENCE_DIR / f"{evidence.source_id}.json"
    target.write_text(
        json.dumps(asdict(evidence), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
