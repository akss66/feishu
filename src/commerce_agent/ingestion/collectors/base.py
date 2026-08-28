from __future__ import annotations

from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass, field
from ipaddress import IPv6Address, ip_address
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
from commerce_agent.ingestion.security import canonical_hostname

DEFAULT_ITEM_LIMIT = 100
_METADATA_HOSTS = frozenset(
    {
        "instance-data.ec2.internal",
        "metadata.aws.internal",
        "metadata.azure.internal",
        "metadata.google.internal",
        "metadata.goog",
    }
)
_METADATA_IPS = frozenset({"100.100.100.200", "169.254.169.254", "169.254.170.2"})
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
        "rate_limited",
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
    configured = source.collector_config.get("allowed_hosts")
    if isinstance(configured, str):
        normalized_hosts: list[str] = []
        for token in configured.split(","):
            if not token.strip():
                continue
            normalized = canonical_hostname(token.strip(), required=False)
            if normalized is None:
                raise CollectorError("invalid_config")
            normalized_hosts.append(normalized)
        return tuple(dict.fromkeys(normalized_hosts))
    host = urlsplit(source.entry_url).hostname
    normalized = canonical_hostname(host, required=False)
    if normalized is None:
        raise CollectorError("invalid_config")
    return (normalized,)


def item_limit(source: SourceDefinition) -> int:
    value = source.collector_config.get("item_limit", DEFAULT_ITEM_LIMIT)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CollectorError("invalid_config")
    return value


def candidate_url(base_url: str, raw_url: str | None) -> str | None:
    if raw_url is None or not raw_url.strip():
        return None
    try:
        absolute = urljoin(base_url, raw_url.strip())
        parsed = urlsplit(absolute)
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if _is_forbidden_discovered_host(parsed.hostname):
        return None
    return absolute


def _is_forbidden_discovered_host(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    if (
        host == "localhost"
        or host.endswith(".localhost")
        or host in _METADATA_HOSTS
        or host in _METADATA_IPS
    ):
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not address.is_global or address.is_multicast


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
        source_id=source.source_id,
        circuit=context.circuit,
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
