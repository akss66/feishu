import json
from collections.abc import Callable

import httpx
import pytest

from commerce_agent.ingestion.dns import CloudflareDohResolver, DohResolutionError


def dns_payload(
    answers: list[dict[str, object]] | None = None,
    *,
    status: int = 0,
    truncated: bool = False,
) -> dict[str, object]:
    return {
        "Status": status,
        "TC": truncated,
        "Answer": answers or [],
    }


def resolver_for(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_response_bytes: int = 65_536,
) -> CloudflareDohResolver:
    return CloudflareDohResolver(
        transport=httpx.MockTransport(handler),
        max_response_bytes=max_response_bytes,
    )


@pytest.mark.asyncio
async def test_doh_resolver_collects_a_and_aaaa_addresses_and_ignores_cname() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        record_type = request.url.params["type"]
        answers = {
            "A": [
                {"type": 5, "data": "edge.example.test"},
                {"type": 1, "data": "93.184.216.34"},
                {"type": 1, "data": "93.184.216.34"},
            ],
            "AAAA": [{"type": 28, "data": "2606:2800:220:1:248:1893:25c8:1946"}],
        }[record_type]
        return httpx.Response(
            200,
            headers={"content-type": "application/dns-json; charset=utf-8"},
            json=dns_payload(answers),
        )

    resolver = resolver_for(handler)
    try:
        result = await resolver("example.com")
    finally:
        await resolver.aclose()

    assert result == (
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    )
    assert [request.url.params["type"] for request in requests] == ["A", "AAAA"]
    assert all(request.url.host == "1.1.1.1" for request in requests)
    assert all(request.url.path == "/dns-query" for request in requests)
    assert all(request.url.params["name"] == "example.com" for request in requests)
    assert all(request.headers["accept"] == "application/dns-json" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answers",
    [
        {"A": [{"type": 1, "data": "93.184.216.34"}], "AAAA": []},
        {"A": [], "AAAA": [{"type": 28, "data": "2001:4860:4860::8888"}]},
    ],
)
async def test_doh_resolver_accepts_single_address_family(
    answers: dict[str, list[dict[str, object]]],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/dns-json"},
            json=dns_payload(answers[request.url.params["type"]]),
        )

    resolver = resolver_for(handler)
    try:
        result = await resolver("example.com")
    finally:
        await resolver.aclose()

    assert len(result) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_factory", "max_response_bytes"),
    [
        (lambda: httpx.Response(503, text="upstream unavailable"), 65_536),
        (
            lambda: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html>not dns json</html>",
            ),
            65_536,
        ),
        (
            lambda: httpx.Response(
                200,
                headers={"content-type": "application/dns-json"},
                content=b"x" * 65,
            ),
            64,
        ),
        (
            lambda: httpx.Response(
                200,
                headers={"content-type": "application/dns-json"},
                content=b"not-json",
            ),
            65_536,
        ),
        (
            lambda: httpx.Response(
                200,
                headers={"content-type": "application/dns-json"},
                json=dns_payload(status=3),
            ),
            65_536,
        ),
        (
            lambda: httpx.Response(
                200,
                headers={"content-type": "application/dns-json"},
                json=dns_payload(status=2),
            ),
            65_536,
        ),
        (
            lambda: httpx.Response(
                200,
                headers={"content-type": "application/dns-json"},
                json=dns_payload(truncated=True),
            ),
            65_536,
        ),
        (
            lambda: httpx.Response(
                200,
                headers={"content-type": "application/dns-json"},
                json=dns_payload([{"type": 1, "data": "not-an-ip"}]),
            ),
            65_536,
        ),
    ],
)
async def test_doh_resolver_fails_closed_without_leaking_response_details(
    response_factory: Callable[[], httpx.Response],
    max_response_bytes: int,
) -> None:
    resolver = resolver_for(
        lambda _request: response_factory(),
        max_response_bytes=max_response_bytes,
    )
    try:
        with pytest.raises(DohResolutionError) as error:
            await resolver("example.com")
    finally:
        await resolver.aclose()

    assert str(error.value) == "dns_resolution_failed"
    assert "upstream" not in repr(error.value)
    assert "not-an-ip" not in repr(error.value)


@pytest.mark.asyncio
async def test_doh_resolver_rejects_empty_combined_result() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/dns-json"},
            json=dns_payload(),
        )

    resolver = resolver_for(handler)
    try:
        with pytest.raises(DohResolutionError, match="^dns_resolution_failed$"):
            await resolver("example.com")
    finally:
        await resolver.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["connect", "timeout"])
async def test_doh_resolver_wraps_transport_errors_and_close_is_idempotent(
    failure_kind: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        error_type = httpx.ConnectError if failure_kind == "connect" else httpx.ReadTimeout
        raise error_type("sensitive network detail", request=request)

    resolver = resolver_for(handler)

    with pytest.raises(DohResolutionError) as error:
        await resolver("example.com")

    await resolver.aclose()
    await resolver.aclose()
    assert str(error.value) == "dns_resolution_failed"
    assert "sensitive" not in repr(error.value)


@pytest.mark.asyncio
async def test_doh_resolver_rejects_non_object_json() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/dns-json"},
            content=json.dumps([{"Status": 0}]).encode(),
        )

    resolver = resolver_for(handler)
    try:
        with pytest.raises(DohResolutionError, match="^dns_resolution_failed$"):
            await resolver("example.com")
    finally:
        await resolver.aclose()
