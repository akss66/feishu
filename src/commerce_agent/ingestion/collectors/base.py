from __future__ import annotations

from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from urllib.parse import urljoin, urlsplit

from commerce_agent.ingestion.http import FetchRequest, FetchResponse
from commerce_agent.ingestion.models import (
    CollectedFailure,
    CollectedItem,
    FetchContext,
    FetchMetrics,
    ResponseArtifact,
    SourceDefinition,
)

DEFAULT_ITEM_LIMIT = 100
_CONTROLLED_DETAIL_ERROR_CODES = frozenset(
    {
        "compliance_review_required",
        "destination_not_public",
        "dns_resolution_failed",
        "fetch_failed",
        "host_not_allowed",
        "http_client_error",
        "http_transport_error",
        "invalid_url",
        "network_retry_exhausted",
        "port_not_allowed",
        "redirect_missing_location",
        "redirect_status_not_supported",
        "renderer_failed",
        "renderer_response_unavailable",
        "renderer_security_rejected",
        "renderer_timeout",
        "renderer_unavailable",
        "response_too_large",
        "retry_exhausted",
        "scheme_not_allowed",
        "too_many_redirects",
        "unexpected_http_status",
        "userinfo_not_allowed",
    }
)


class CollectorError(RuntimeError):
    """Stable, secret-free classification for collector failures."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"collector failed: {code}")


class HttpPort(Protocol):
    async def get(self, request: FetchRequest) -> FetchResponse: ...


@dataclass(frozen=True, slots=True)
class BrowserRequest:
    url: str
    allowed_hosts: Collection[str]
    timeout_seconds: float
    metrics: FetchMetrics = field(default_factory=FetchMetrics, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        object.__setattr__(self, "allowed_hosts", tuple(self.allowed_hosts))


@dataclass(frozen=True, slots=True)
class RenderedPage:
    url: str
    body: bytes
    artifact: ResponseArtifact
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", bytes(self.body))
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


class BrowserPort(Protocol):
    async def render(self, request: BrowserRequest) -> RenderedPage: ...


@runtime_checkable
class Collector(Protocol):
    def collect(
        self,
        source: SourceDefinition,
        context: FetchContext,
    ) -> AsyncIterator[CollectedItem | CollectedFailure]: ...


def allowed_hosts(source: SourceDefinition) -> tuple[str, ...]:
    host = urlsplit(source.entry_url).hostname
    if host is None:
        raise CollectorError("invalid_config")
    return (host.rstrip(".").lower(),)


def item_limit(source: SourceDefinition) -> int:
    value = source.collector_config.get("item_limit", DEFAULT_ITEM_LIMIT)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CollectorError("invalid_config")
    return value


def candidate_url(base_url: str, raw_url: str | None) -> str | None:
    if raw_url is None or not raw_url.strip():
        return None
    absolute = urljoin(base_url, raw_url.strip())
    parsed = urlsplit(absolute)
    if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return absolute


def fetch_request(
    source: SourceDefinition,
    context: FetchContext,
    *,
    url: str | None = None,
    conditional: bool = True,
) -> FetchRequest:
    return FetchRequest(
        url=url or source.entry_url,
        allowed_hosts=allowed_hosts(source),
        etag=context.etag if conditional else None,
        last_modified=context.last_modified if conditional else None,
        metrics=context.metrics,
    )


def require_success(response: FetchResponse) -> bool:
    if response.not_modified:
        return False
    if not 200 <= response.status_code < 300:
        raise CollectorError("fetch_failed")
    return True


def response_artifact(response: FetchResponse) -> ResponseArtifact:
    return ResponseArtifact(
        url=response.url,
        status_code=response.status_code,
        headers=response.headers,
        body=response.body,
    )


def detail_failure(error: BaseException) -> CollectedFailure:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code in _CONTROLLED_DETAIL_ERROR_CODES:
        return CollectedFailure(code)
    return CollectedFailure("detail_fetch_failed")
