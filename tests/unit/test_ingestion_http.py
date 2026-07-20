from __future__ import annotations

# ruff: noqa: ASYNC109
import asyncio
import logging
from collections.abc import AsyncIterator, Collection

import httpcore
import httpx
import pytest

from commerce_agent.ingestion.http import (
    _RESOLVED_ADDRESSES_EXTENSION,
    FetchError,
    FetchRequest,
    IngestionHttpClient,
    _PinnedAsyncTransport,
)
from commerce_agent.ingestion.security import UrlSafetyError, UrlSafetyPolicy

PUBLIC_IP = "93.184.216.34"


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: Collection[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def policy(
    addresses: dict[str, Collection[str]] | None = None,
    *,
    resolved_hosts: list[str] | None = None,
) -> UrlSafetyPolicy:
    configured = addresses or {}

    async def resolve(host: str) -> Collection[str]:
        if resolved_hosts is not None:
            resolved_hosts.append(host)
        return configured.get(host, (PUBLIC_IP,))

    return UrlSafetyPolicy(resolver=resolve)


def fetch(url: str = "https://news.example.com/items") -> FetchRequest:
    return FetchRequest(url=url, allowed_hosts=("news.example.com",))


async def test_configures_twenty_second_timeout_on_shared_client_requests() -> None:
    seen_timeouts: list[dict[str, float]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, content=b"ok")

    async with IngestionHttpClient(
        safety_policy=policy(),
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.get(fetch())
        await client.get(fetch())

    assert len(seen_timeouts) == 2
    assert all(set(timeout.values()) == {20.0} for timeout in seen_timeouts)


async def test_global_semaphore_bounds_in_flight_requests() -> None:
    active = 0
    maximum_active = 0
    two_entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        if active == 2:
            two_entered.set()
        await release.wait()
        active -= 1
        return httpx.Response(200, content=b"ok")

    requests = [
        FetchRequest(f"https://host-{index}.example/items", (f"host-{index}.example",))
        for index in range(3)
    ]
    async with IngestionHttpClient(
        safety_policy=policy(),
        global_concurrency=2,
        transport=httpx.MockTransport(handler),
    ) as client:
        tasks = [asyncio.create_task(client.get(request)) for request in requests]
        await asyncio.wait_for(two_entered.wait(), timeout=1)
        await asyncio.sleep(0)
        assert maximum_active == 2
        release.set()
        await asyncio.gather(*tasks)


async def test_same_domain_requests_are_spaced_by_at_least_one_second() -> None:
    fake_time = FakeTime()
    starts: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        starts.append(fake_time.monotonic())
        return httpx.Response(200, content=b"ok")

    async with IngestionHttpClient(
        safety_policy=policy(),
        domain_rps=1.0,
        clock=fake_time.monotonic,
        sleeper=fake_time.sleep,
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.get(fetch())
        await client.get(fetch())
        await client.get(fetch())

    assert starts == [0.0, 1.0, 2.0]
    assert fake_time.sleeps == [1.0, 1.0]


async def test_redirect_is_manually_revalidated_before_next_network_call() -> None:
    resolved_hosts: list[str] = []
    seen_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        if len(seen_urls) == 1:
            return httpx.Response(302, headers={"Location": "https://cdn.example.com/final"})
        return httpx.Response(200, content=b"ok")

    request = FetchRequest(
        "https://news.example.com/start",
        ("news.example.com", "cdn.example.com"),
    )
    async with IngestionHttpClient(
        safety_policy=policy(resolved_hosts=resolved_hosts),
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client.get(request)

    assert response.url == "https://cdn.example.com/final"
    assert seen_urls == ["https://news.example.com/start", "https://cdn.example.com/final"]
    assert resolved_hosts == ["news.example.com", "cdn.example.com"]


async def test_redirect_to_private_resolution_is_rejected_without_following() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "https://cdn.example.com/final"})

    request = FetchRequest(
        "https://news.example.com/start",
        ("news.example.com", "cdn.example.com"),
    )
    async with IngestionHttpClient(
        safety_policy=policy({"cdn.example.com": ("127.0.0.1",)}),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(UrlSafetyError, match="destination_not_public"):
            await client.get(request)

    assert calls == 1


async def test_conditional_headers_and_not_modified_response() -> None:
    seen_headers: httpx.Headers | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_headers
        seen_headers = request.headers
        return httpx.Response(
            304,
            headers={"ETag": '"new"', "Last-Modified": "Mon, 20 Jul 2026 00:00:00 GMT"},
        )

    request = FetchRequest(
        "https://news.example.com/items",
        ("news.example.com",),
        etag='"old"',
        last_modified="Sun, 19 Jul 2026 00:00:00 GMT",
    )
    async with IngestionHttpClient(
        safety_policy=policy(),
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client.get(request)

    assert seen_headers is not None
    assert seen_headers["If-None-Match"] == '"old"'
    assert seen_headers["If-Modified-Since"] == "Sun, 19 Jul 2026 00:00:00 GMT"
    assert response.not_modified is True
    assert response.body == b""
    assert response.etag == '"new"'


async def test_streamed_response_aborts_above_ten_mib_and_closes_response() -> None:
    stream = TrackingStream((b"a" * 10_485_760, b"b"))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with IngestionHttpClient(
        safety_policy=policy(),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(FetchError) as caught:
            await client.get(fetch())

    assert caught.value.code == "response_too_large"
    assert stream.closed is True


async def test_429_retry_respects_retry_after() -> None:
    fake_time = FakeTime()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, content=b"ok")

    async with IngestionHttpClient(
        safety_policy=policy(),
        clock=fake_time.monotonic,
        sleeper=fake_time.sleep,
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client.get(fetch())

    assert response.body == b"ok"
    assert calls == 2
    assert fake_time.sleeps == [3.0]


async def test_5xx_and_transient_connection_errors_are_retried() -> None:
    fake_time = FakeTime()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("connection reset", request=request)
        if calls == 2:
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok")

    async with IngestionHttpClient(
        safety_policy=policy(),
        clock=fake_time.monotonic,
        sleeper=fake_time.sleep,
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client.get(fetch())

    assert response.body == b"ok"
    assert calls == 3
    assert fake_time.sleeps == [1.0, 2.0]


async def test_transient_write_error_is_retried() -> None:
    fake_time = FakeTime()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.WriteError("connection closed while sending", request=request)
        return httpx.Response(200, content=b"ok")

    async with IngestionHttpClient(
        safety_policy=policy(),
        clock=fake_time.monotonic,
        sleeper=fake_time.sleep,
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client.get(fetch())

    assert response.body == b"ok"
    assert calls == 2
    assert fake_time.sleeps == [1.0]


async def test_nontransient_httpx_error_is_wrapped_in_stable_classification() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.LocalProtocolError(
            "malformed request with top-secret-value",
            request=request,
        )

    async with IngestionHttpClient(
        safety_policy=policy(),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(FetchError) as caught:
            await client.get(fetch())

    assert caught.value.code == "http_transport_error"
    assert "top-secret-value" not in str(caught.value)


async def test_retryable_status_has_at_most_three_retries_and_closes_each_response() -> None:
    fake_time = FakeTime()
    streams: list[TrackingStream] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        stream = TrackingStream(())
        streams.append(stream)
        return httpx.Response(503, stream=stream)

    async with IngestionHttpClient(
        safety_policy=policy(),
        clock=fake_time.monotonic,
        sleeper=fake_time.sleep,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(FetchError) as caught:
            await client.get(fetch())

    assert caught.value.code == "retry_exhausted"
    assert caught.value.status_code == 503
    assert len(streams) == 4
    assert all(stream.closed for stream in streams)


async def test_ordinary_4xx_is_not_retried() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    async with IngestionHttpClient(
        safety_policy=policy(),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(FetchError) as caught:
            await client.get(fetch())

    assert caught.value.code == "http_client_error"
    assert caught.value.status_code == 404
    assert calls == 1


async def test_logs_never_include_query_secrets_or_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "top-secret-value"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=secret.encode())

    request = FetchRequest(
        f"https://news.example.com/items?access_token={secret}",
        ("news.example.com",),
    )
    caplog.set_level(logging.INFO, logger="commerce_agent.ingestion.http")
    async with IngestionHttpClient(
        safety_policy=policy(),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(FetchError):
            await client.get(request)

    assert secret not in caplog.text
    assert "access_token" not in caplog.text


class FakeNetworkStream(httpcore.AsyncNetworkStream):
    def __init__(self, response_count: int = 1) -> None:
        self._responses = [
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
            for _ in range(response_count)
        ]
        self.server_hostname: str | None = None

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._responses.pop(0) if self._responses else b""

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: object,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.server_hostname = server_hostname
        return self

    def get_extra_info(self, info: str) -> object:
        return None


class FakeNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, *, response_count: int = 1) -> None:
        self.connected_hosts: list[str] = []
        self.stream = FakeNetworkStream(response_count)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Collection[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connected_hosts.append(host)
        return self.stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Collection[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("Unix sockets must not be used")

    async def sleep(self, seconds: float) -> None:
        return None


async def test_default_transport_connects_validated_ip_but_keeps_hostname_for_tls() -> None:
    backend = FakeNetworkBackend()
    transport = _PinnedAsyncTransport(network_backend=backend)
    async with httpx.AsyncClient(transport=transport) as client:
        request = client.build_request("GET", "https://news.example.com/items")
        request.extensions[_RESOLVED_ADDRESSES_EXTENSION] = (PUBLIC_IP,)
        response = await client.send(request)

    assert response.content == b"ok"
    assert backend.connected_hosts == [PUBLIC_IP]
    assert backend.stream.server_hostname == "news.example.com"


async def test_default_transport_rejects_unvalidated_connection_attempt() -> None:
    backend = FakeNetworkBackend()
    transport = _PinnedAsyncTransport(network_backend=backend)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError, match="destination_not_validated"):
            await client.get("https://news.example.com/items")

    assert backend.connected_hosts == []


async def test_default_transport_reuses_a_connection_that_was_opened_to_a_validated_ip() -> None:
    backend = FakeNetworkBackend(response_count=2)
    transport = _PinnedAsyncTransport(network_backend=backend)
    async with httpx.AsyncClient(transport=transport) as client:
        for _ in range(2):
            request = client.build_request("GET", "https://news.example.com/items")
            request.extensions[_RESOLVED_ADDRESSES_EXTENSION] = (PUBLIC_IP,)
            response = await client.send(request)
            assert response.content == b"ok"

    assert backend.connected_hosts == [PUBLIC_IP]


class ConcurrentNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.connected_hosts: list[str] = []
        self.streams: list[FakeNetworkStream] = []
        self._two_entered = asyncio.Event()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Collection[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connected_hosts.append(host)
        stream = FakeNetworkStream()
        self.streams.append(stream)
        if len(self.connected_hosts) == 2:
            self._two_entered.set()
        await self._two_entered.wait()
        return stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Collection[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("Unix sockets must not be used")

    async def sleep(self, seconds: float) -> None:
        return None


async def test_validated_address_context_is_isolated_between_concurrent_hosts() -> None:
    backend = ConcurrentNetworkBackend()
    transport = _PinnedAsyncTransport(network_backend=backend)
    async with httpx.AsyncClient(transport=transport) as client:
        first = client.build_request("GET", "https://one.example/items")
        first.extensions[_RESOLVED_ADDRESSES_EXTENSION] = ("93.184.216.10",)
        second = client.build_request("GET", "https://two.example/items")
        second.extensions[_RESOLVED_ADDRESSES_EXTENSION] = ("93.184.216.20",)
        responses = await asyncio.gather(client.send(first), client.send(second))

    assert [response.content for response in responses] == [b"ok", b"ok"]
    assert set(backend.connected_hosts) == {"93.184.216.10", "93.184.216.20"}
    assert {stream.server_hostname for stream in backend.streams} == {
        "one.example",
        "two.example",
    }
