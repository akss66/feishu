"""Bounded, SSRF-resistant HTTP fetching for public ingestion sources."""

# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from ipaddress import IPv6Address, ip_address
from types import MappingProxyType
from typing import Protocol, cast
from urllib.parse import urljoin

import httpcore
import httpx

from commerce_agent.ingestion.security import SafeUrl, UrlSafetyError, UrlSafetyPolicy

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]

_LOGGER = logging.getLogger(__name__)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SAFE_RESPONSE_HEADERS = frozenset({"content-type", "etag", "last-modified"})
_TRANSIENT_HTTPX_ERRORS = (
    httpx.NetworkError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)
_HTTPCORE_ERRORS = (
    httpcore.TimeoutException,
    httpcore.NetworkError,
    httpcore.ProtocolError,
    httpcore.ProxyError,
    httpcore.UnsupportedProtocol,
)
_RESOLVED_ADDRESSES_EXTENSION = "commerce_agent.resolved_addresses"
_PINNED_DESTINATIONS: ContextVar[Mapping[str, tuple[str, ...]] | None] = ContextVar(
    "commerce_agent_pinned_destinations",
    default=None,
)


@dataclass(frozen=True, slots=True)
class FetchRequest:
    url: str
    allowed_hosts: Collection[str]
    etag: str | None = None
    last_modified: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_hosts", tuple(self.allowed_hosts))
        for value in (self.etag, self.last_modified):
            if value is not None and ("\r" in value or "\n" in value):
                raise ValueError("conditional header values must not contain newlines")


@dataclass(frozen=True, slots=True)
class FetchResponse:
    url: str
    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "body", bytes(self.body))

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304

    @property
    def etag(self) -> str | None:
        return _header_value(self.headers, "etag")

    @property
    def last_modified(self) -> str | None:
        return _header_value(self.headers, "last-modified")


@dataclass(frozen=True, slots=True)
class _AttemptResult:
    status_code: int
    headers: httpx.Headers
    body: bytes


class _ClosableAsyncStream(Protocol):
    def __aiter__(self) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


class FetchError(RuntimeError):
    """A stable, secret-free classification for a failed fetch."""

    def __init__(
        self,
        code: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        suffix = f" (status={status_code})" if status_code is not None else ""
        super().__init__(f"fetch failed: {code}{suffix}")


class _DomainLimiter:
    def __init__(self, requests_per_second: float, clock: Clock, sleeper: Sleeper) -> None:
        if requests_per_second <= 0:
            raise ValueError("domain_rps must be positive")
        self._interval = 1.0 / requests_per_second
        self._clock = clock
        self._sleeper = sleeper
        self._registry_lock = asyncio.Lock()
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_started: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        lock = await self._lock_for(host)
        async with lock:
            now = self._clock()
            previous = self._last_started.get(host)
            if previous is not None:
                delay = previous + self._interval - now
                if delay > 0:
                    await self._sleeper(delay)
            self._last_started[host] = self._clock()

    async def _lock_for(self, host: str) -> asyncio.Lock:
        async with self._registry_lock:
            return self._host_locks.setdefault(host, asyncio.Lock())


class IngestionHttpClient:
    """Shared HTTPX client with bounded streaming, redirects, retries, and rate limits."""

    def __init__(
        self,
        *,
        safety_policy: UrlSafetyPolicy,
        global_concurrency: int = 4,
        domain_rps: float = 1.0,
        timeout_seconds: float = 20.0,
        max_response_bytes: int = 10_485_760,
        user_agent: str = "CrossBorderCommerceAgent/0.1",
        max_retries: int = 3,
        max_redirects: int = 5,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if global_concurrency <= 0:
            raise ValueError("global_concurrency must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if max_retries < 0 or max_redirects < 0:
            raise ValueError("retry and redirect limits must not be negative")
        self._safety_policy = safety_policy
        self._semaphore = asyncio.Semaphore(global_concurrency)
        self._limiter = _DomainLimiter(domain_rps, clock, sleeper)
        self._sleeper = sleeper
        self._max_response_bytes = max_response_bytes
        self._max_retries = max_retries
        self._max_redirects = max_redirects
        self._closed = False
        self._client = httpx.AsyncClient(
            transport=transport or _PinnedAsyncTransport(),
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": user_agent},
            trust_env=False,
        )

    async def __aenter__(self) -> IngestionHttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self._client.aclose()

    async def get(self, request: FetchRequest) -> FetchResponse:
        if self._closed:
            raise RuntimeError("HTTP client is closed")
        current_url = request.url
        try:
            for redirect_count in range(self._max_redirects + 1):
                safe_url = await self._safety_policy.validate(
                    current_url,
                    request.allowed_hosts,
                )
                result = await self._fetch_hop(safe_url, request)
                if result.status_code in _REDIRECT_STATUSES:
                    location = result.headers.get("Location")
                    if not location:
                        raise FetchError("redirect_missing_location")
                    if redirect_count == self._max_redirects:
                        raise FetchError("too_many_redirects")
                    current_url = urljoin(safe_url.url, location)
                    continue
                if 300 <= result.status_code < 400 and result.status_code != 304:
                    raise FetchError(
                        "redirect_status_not_supported",
                        status_code=result.status_code,
                    )
                response = FetchResponse(
                    url=safe_url.url,
                    status_code=result.status_code,
                    headers={
                        name: value
                        for name, value in result.headers.items()
                        if name.lower() in _SAFE_RESPONSE_HEADERS
                    },
                    body=result.body,
                )
                _LOGGER.info(
                    "ingestion fetch completed url=%s status=%d bytes=%d",
                    self._safety_policy.redact_for_log(safe_url.url),
                    response.status_code,
                    len(response.body),
                )
                return response
        except (FetchError, UrlSafetyError) as exc:
            code = exc.code
            _LOGGER.warning(
                "ingestion fetch failed url=%s code=%s",
                self._safety_policy.redact_for_log(current_url),
                code,
            )
            raise
        raise FetchError("too_many_redirects")

    async def _fetch_hop(self, safe_url: SafeUrl, request: FetchRequest) -> _AttemptResult:
        for attempt in range(self._max_retries + 1):
            await self._limiter.wait(safe_url.host)
            try:
                result = await self._send_once(safe_url, request)
            except _TRANSIENT_HTTPX_ERRORS:
                if attempt == self._max_retries:
                    raise FetchError("network_retry_exhausted", retryable=True) from None
                await self._sleeper(_backoff(attempt))
                continue
            except httpx.HTTPError:
                raise FetchError("http_transport_error") from None

            status_code = result.status_code
            if status_code == 429 or 500 <= status_code < 600:
                if attempt == self._max_retries:
                    raise FetchError(
                        "retry_exhausted",
                        status_code=status_code,
                        retryable=True,
                    )
                retry_after = _retry_after_seconds(result.headers.get("Retry-After"))
                await self._sleeper(max(_backoff(attempt), retry_after or 0.0))
                continue
            if status_code in {401, 403}:
                raise FetchError("compliance_review_required", status_code=status_code)
            if 400 <= status_code < 500:
                raise FetchError("http_client_error", status_code=status_code)
            if status_code < 200:
                raise FetchError("unexpected_http_status", status_code=status_code)
            return result
        raise FetchError("retry_exhausted", retryable=True)

    async def _send_once(self, safe_url: SafeUrl, request: FetchRequest) -> _AttemptResult:
        headers: dict[str, str] = {"Accept-Encoding": "gzip, deflate"}
        if request.etag is not None:
            headers["If-None-Match"] = request.etag
        if request.last_modified is not None:
            headers["If-Modified-Since"] = request.last_modified

        httpx_request = self._client.build_request("GET", safe_url.url, headers=headers)
        for sensitive_header in ("Authorization", "Cookie", "Proxy-Authorization"):
            httpx_request.headers.pop(sensitive_header, None)
        httpx_request.extensions[_RESOLVED_ADDRESSES_EXTENSION] = safe_url.resolved_addresses

        async with self._semaphore:
            response = await self._client.send(
                httpx_request,
                stream=True,
                follow_redirects=False,
            )
            try:
                content_length = _content_length(response.headers)
                if content_length is not None and content_length > self._max_response_bytes:
                    raise FetchError("response_too_large")
                if (
                    response.status_code in _REDIRECT_STATUSES
                    or response.status_code == 304
                    or response.status_code == 429
                    or response.status_code >= 400
                ):
                    body = b""
                else:
                    body = await self._read_bounded(response)
                return _AttemptResult(response.status_code, response.headers, body)
            finally:
                await response.aclose()

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > self._max_response_bytes:
                raise FetchError("response_too_large")
            body.extend(chunk)
        return bytes(body)


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect only to addresses validated for the current request context."""

    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Collection[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        destinations = _PINNED_DESTINATIONS.get()
        normalized_host = host.rstrip(".").lower()
        addresses = destinations.get(normalized_host) if destinations is not None else None
        if port != 443 or not addresses:
            raise httpcore.ConnectError("destination_not_validated")

        last_error: Exception | None = None
        for address in _require_public_ip_addresses(addresses):
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise httpcore.ConnectError("destination_not_validated")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Collection[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("unix_sockets_not_allowed")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _PinnedAsyncTransport(httpx.AsyncBaseTransport):
    """HTTPX transport backed by an httpcore pool with validated DNS pinning."""

    def __init__(self, network_backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            network_backend=_PinnedNetworkBackend(network_backend),
            retries=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        addresses = request.extensions.get(_RESOLVED_ADDRESSES_EXTENSION)
        if not isinstance(addresses, tuple) or not all(
            isinstance(address, str) for address in addresses
        ):
            addresses = ()
        host = request.url.host.rstrip(".").lower()
        token = _PINNED_DESTINATIONS.set({host: addresses})
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            try:
                response = await self._pool.handle_async_request(core_request)
            except _HTTPCORE_ERRORS as exc:
                raise _as_httpx_exception(exc, request) from exc
        finally:
            _PINNED_DESTINATIONS.reset(token)
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_CoreResponseStream(cast(_ClosableAsyncStream, response.stream), request),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


class _CoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: _ClosableAsyncStream, request: httpx.Request) -> None:
        self._stream = stream
        self._request = request

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._stream:
                yield chunk
        except Exception as exc:
            mapped = _as_httpx_exception(exc, self._request)
            if mapped is exc:
                raise
            raise mapped from exc

    async def aclose(self) -> None:
        try:
            await self._stream.aclose()
        except Exception as exc:
            mapped = _as_httpx_exception(exc, self._request)
            if mapped is exc:
                raise
            raise mapped from exc


def _as_httpx_exception(exc: Exception, request: httpx.Request) -> Exception:
    mappings: tuple[tuple[type[Exception], type[httpx.RequestError]], ...] = (
        (httpcore.ConnectTimeout, httpx.ConnectTimeout),
        (httpcore.ReadTimeout, httpx.ReadTimeout),
        (httpcore.WriteTimeout, httpx.WriteTimeout),
        (httpcore.PoolTimeout, httpx.PoolTimeout),
        (httpcore.ConnectError, httpx.ConnectError),
        (httpcore.ReadError, httpx.ReadError),
        (httpcore.WriteError, httpx.WriteError),
        (httpcore.LocalProtocolError, httpx.LocalProtocolError),
        (httpcore.RemoteProtocolError, httpx.RemoteProtocolError),
        (httpcore.ProxyError, httpx.ProxyError),
        (httpcore.UnsupportedProtocol, httpx.UnsupportedProtocol),
    )
    for core_type, httpx_type in mappings:
        if isinstance(exc, core_type):
            return httpx_type(str(exc), request=request)
    return exc


def _require_public_ip_addresses(addresses: tuple[str, ...]) -> tuple[str, ...]:
    validated: list[str] = []
    for raw_address in addresses:
        try:
            address = ip_address(raw_address)
        except ValueError:
            raise httpcore.ConnectError("destination_not_validated") from None
        if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if not address.is_global or address.is_multicast:
            raise httpcore.ConnectError("destination_not_validated")
        validated.append(str(address))
    if not validated:
        raise httpcore.ConnectError("destination_not_validated")
    return tuple(validated)


def _content_length(headers: httpx.Headers) -> int | None:
    value = headers.get("Content-Length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            return None
        return max(0.0, retry_at.timestamp() - time.time())


def _backoff(attempt: int) -> float:
    return float(2**attempt)


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    return next((value for key, value in headers.items() if key.lower() == lowered), None)
