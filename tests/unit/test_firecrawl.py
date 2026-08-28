from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from commerce_agent.integrations.firecrawl import (
    FirecrawlClient,
    FirecrawlError,
)


def response(payload: object, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


async def test_scrape_posts_v2_markdown_request_without_exposing_key() -> None:
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(
            {
                "success": True,
                "data": {
                    "markdown": "# Seller update\n\nNew policy text.",
                    "metadata": {
                        "title": "Seller update",
                        "sourceURL": "https://seller.example.com/news",
                        "statusCode": 200,
                        "cacheState": "miss",
                    },
                },
            }
        )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        base_url="https://api.firecrawl.dev",
    )
    client = FirecrawlClient(
        api_key=SecretStr("fc-super-secret"),
        api_url="https://api.firecrawl.dev",
        timeout_seconds=12,
        max_age_ms=900_000,
        http_client=http,
    )

    document = await client.scrape("https://seller.example.com/news")

    assert document.url == "https://seller.example.com/news"
    assert document.title == "Seller update"
    assert document.markdown.startswith("# Seller update")
    assert document.status_code == 200
    assert document.cache_state == "miss"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url == httpx.URL("https://api.firecrawl.dev/v2/scrape")
    assert request.headers["authorization"] == "Bearer fc-super-secret"
    assert json.loads(request.content) == {
        "url": "https://seller.example.com/news",
        "formats": ["markdown"],
        "onlyMainContent": True,
        "maxAge": 900_000,
    }
    assert "fc-super-secret" not in repr(client)
    await http.aclose()


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, "firecrawl_auth_failed"),
        (403, "firecrawl_auth_failed"),
        (429, "firecrawl_rate_limited"),
    ],
)
async def test_scrape_classifies_expected_http_failures(
    status_code: int,
    expected_code: str,
) -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: response(
                {"success": False, "error": "do not leak"},
                status_code=status_code,
            )
        ),
        base_url="https://api.firecrawl.dev",
    )
    client = FirecrawlClient(
        api_key=SecretStr("fc-super-secret"),
        http_client=http,
        max_attempts=1,
    )

    with pytest.raises(FirecrawlError) as captured:
        await client.scrape("https://seller.example.com/news")

    assert captured.value.code == expected_code
    assert "do not leak" not in str(captured.value)
    assert "fc-super-secret" not in str(captured.value)
    await http.aclose()


async def test_scrape_honors_retry_after_then_returns_recovered_document() -> None:
    attempts = 0
    delays: list[float] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                json={"success": False},
                headers={"retry-after": "2"},
            )
        return response(
            {
                "success": True,
                "data": {
                    "markdown": "# Recovered",
                    "metadata": {"sourceURL": "https://seller.example.com/news"},
                },
            }
        )

    async def sleep(delay: float) -> None:
        delays.append(delay)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        base_url="https://api.firecrawl.dev",
    )
    client = FirecrawlClient(
        api_key=SecretStr("fc-test"),
        http_client=http,
        max_attempts=3,
        min_request_interval_seconds=0,
        sleep=sleep,
    )

    document = await client.scrape("https://seller.example.com/news")

    assert document.markdown == "# Recovered"
    assert attempts == 2
    assert delays == [2.0]
    await http.aclose()


async def test_scrape_limits_firecrawl_concurrency_independently() -> None:
    active = 0
    max_active = 0
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if not first_started.is_set():
            first_started.set()
            await release_first.wait()
        active -= 1
        return response(
            {
                "success": True,
                "data": {
                    "markdown": "# Result",
                    "metadata": {"sourceURL": str(request.url)},
                },
            }
        )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        base_url="https://api.firecrawl.dev",
    )
    client = FirecrawlClient(
        api_key=SecretStr("fc-test"),
        http_client=http,
        max_concurrency=1,
        min_request_interval_seconds=0,
    )

    first = asyncio.create_task(client.scrape("https://seller.example.com/one"))
    await first_started.wait()
    second = asyncio.create_task(client.scrape("https://seller.example.com/two"))
    await asyncio.sleep(0)

    assert max_active == 1
    release_first.set()
    await asyncio.gather(first, second)
    assert max_active == 1
    await http.aclose()


async def test_scrape_spaces_requests_to_respect_per_minute_limits() -> None:
    now = 100.0
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        nonlocal now
        delays.append(delay)
        now += delay

    async def handle(request: httpx.Request) -> httpx.Response:
        return response(
            {
                "success": True,
                "data": {
                    "markdown": "# Result",
                    "metadata": {"sourceURL": str(request.url)},
                },
            }
        )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        base_url="https://api.firecrawl.dev",
    )
    client = FirecrawlClient(
        api_key=SecretStr("fc-test"),
        http_client=http,
        min_request_interval_seconds=6.5,
        sleep=sleep,
        monotonic=lambda: now,
    )

    await client.scrape("https://seller.example.com/one")
    await client.scrape("https://seller.example.com/two")

    assert delays == [6.5]
    await http.aclose()


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False},
        {"success": True, "data": {}},
        {"success": True, "data": {"markdown": "   ", "metadata": {}}},
        {"success": True, "data": {"markdown": "ok", "metadata": "invalid"}},
    ],
)
async def test_scrape_rejects_invalid_or_blank_payload(payload: object) -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response(payload)),
        base_url="https://api.firecrawl.dev",
    )
    client = FirecrawlClient(api_key=SecretStr("fc-test"), http_client=http)

    with pytest.raises(FirecrawlError) as captured:
        await client.scrape("https://seller.example.com/news")

    assert captured.value.code == "firecrawl_invalid_response"
    await http.aclose()


async def test_scrape_classifies_transport_failure_without_leaking_details() -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret upstream detail", request=request)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(fail),
        base_url="https://api.firecrawl.dev",
    )
    client = FirecrawlClient(api_key=SecretStr("fc-super-secret"), http_client=http)

    with pytest.raises(FirecrawlError) as captured:
        await client.scrape("https://seller.example.com/news")

    assert captured.value.code == "firecrawl_transport_error"
    assert "secret upstream detail" not in str(captured.value)
    await http.aclose()
