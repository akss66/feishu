from __future__ import annotations

from _thread import LockType
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from threading import Lock
from types import MappingProxyType

Scalar = str | int | float | bool | None
_SAFE_ARTIFACT_HEADERS = frozenset({"content-type", "etag", "last-modified"})
_MAX_COUNTER = 2**63 - 1
_METRIC_FIELDS = frozenset({"http_requests", "http_not_modified", "bytes_received"})


class Platform(StrEnum):
    AMAZON = "amazon"
    TEMU = "temu"
    SHEIN = "shein"
    ALIEXPRESS = "aliexpress"
    SHOPEE = "shopee"
    EBAY = "ebay"
    COUPANG = "coupang"
    OZON = "ozon"
    JOYBUY = "joybuy"
    TIKTOK_SHOP = "tiktok_shop"


class CollectorKind(StrEnum):
    RSS = "rss"
    SITEMAP = "sitemap"
    HTML = "html"
    API = "api"
    BROWSER = "browser"


class TrustTier(StrEnum):
    OFFICIAL = "official"
    MEDIA = "media"


class SourceAdapter(StrEnum):
    GENERIC = "generic"
    GDELT = "gdelt"


class ContentScope(StrEnum):
    METADATA_ONLY = "metadata_only"
    FEED_SUMMARY = "feed_summary"
    FULL_TEXT = "full_text"


class ComplianceStatus(StrEnum):
    ALLOWED = "allowed"
    PENDING_REVIEW = "pending_review"
    DENIED = "denied"
    AUTHORIZATION_REQUIRED = "authorization_required"


class CoverageStatus(StrEnum):
    OFFICIAL_PUBLIC_COVERED = "official_public_covered"
    PUBLIC_COVERED_SELLER_CENTER_PENDING = "public_covered_seller_center_pending"
    PARTIAL = "partial"
    ERROR = "error"
    UNCONNECTED = "unconnected"


class Trigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class RunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    source_id: str
    name: str
    entry_url: str
    platforms: tuple[Platform, ...]
    trust_tier: TrustTier
    collector: CollectorKind
    compliance: ComplianceStatus
    enabled: bool
    regions: tuple[str, ...]
    language_hint: str | None
    interval_minutes: int
    terms_url: str
    robots_url: str
    reviewed_at: date
    compliance_notes: str
    adapter: SourceAdapter = SourceAdapter.GENERIC
    content_scope: ContentScope | None = None
    attribution: str | None = None
    publisher_key: str | None = None
    collector_config: Mapping[str, Scalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "platforms", tuple(self.platforms))
        object.__setattr__(self, "regions", tuple(self.regions))
        object.__setattr__(
            self,
            "collector_config",
            MappingProxyType(dict(self.collector_config)),
        )

    @property
    def id(self) -> str:
        """Compatibility alias used by persistence-facing code."""

        return self.source_id

    @property
    def base_url(self) -> str:
        """The configured public entry point for this source."""

        return self.entry_url


@dataclass(slots=True)
class FetchMetrics:
    http_requests: int = 0
    http_not_modified: int = 0
    bytes_received: int = 0
    _lock: LockType = field(default_factory=Lock, init=False, compare=False, repr=False)

    def __setattr__(self, name: str, value: object) -> None:
        if name in _METRIC_FIELDS:
            value = _safe_counter(name, value)
        object.__setattr__(self, name, value)

    def record_request(
        self,
        *,
        status_code: int | None,
        bytes_received: int,
    ) -> None:
        if status_code is not None:
            if not isinstance(status_code, int) or isinstance(status_code, bool):
                raise TypeError("status_code must be an integer or None")
            if not 100 <= status_code <= 599:
                raise ValueError("status_code must be a valid HTTP status")
        response_bytes = _safe_counter("bytes_received", bytes_received)
        with self._lock:
            next_requests = _safe_counter("http_requests", self.http_requests + 1)
            next_not_modified = _safe_counter(
                "http_not_modified",
                self.http_not_modified + (status_code == 304),
            )
            next_bytes = _safe_counter(
                "bytes_received",
                self.bytes_received + response_bytes,
            )
            self.http_requests = next_requests
            self.http_not_modified = next_not_modified
            self.bytes_received = next_bytes


@dataclass(frozen=True, slots=True)
class FetchContext:
    trigger: Trigger
    started_at: datetime
    etag: str | None = None
    last_modified: str | None = None
    metrics: FetchMetrics = field(default_factory=FetchMetrics)


@dataclass(frozen=True, slots=True)
class ResponseArtifact:
    url: str
    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def __post_init__(self) -> None:
        safe_headers = {
            key.lower(): value
            for key, value in self.headers.items()
            if isinstance(key, str)
            and isinstance(value, str)
            and key.lower() in _SAFE_ARTIFACT_HEADERS
        }
        object.__setattr__(self, "headers", MappingProxyType(safe_headers))
        object.__setattr__(self, "body", bytes(self.body))


@dataclass(frozen=True, slots=True)
class CollectedFailure:
    error_code: str


@dataclass(frozen=True, slots=True)
class CollectedItem:
    url: str
    body: bytes
    content_type: str | None = None
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    publisher_key: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    artifact: ResponseArtifact | None = None


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    source_id: str
    canonical_url: str
    title: str
    body: str
    language: str
    language_confidence: float
    fetched_at: datetime
    author: str | None = None
    published_at: datetime | None = None
    metadata: Mapping[str, Scalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class RunSummary:
    source_id: str
    trigger: Trigger
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    discovered: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    error_code: str | None = None
    http_requests: int = 0
    http_not_modified: int = 0
    bytes_received: int = 0
    error_summary: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "discovered",
            "created",
            "updated",
            "skipped",
            "failed",
            "http_requests",
            "http_not_modified",
            "bytes_received",
        ):
            _safe_counter(name, getattr(self, name))
        if self.error_summary is not None:
            if not isinstance(self.error_summary, str):
                raise TypeError("error_summary must be a string")
            if (
                len(self.error_summary) > 512
                or "\r" in self.error_summary
                or "\n" in self.error_summary
            ):
                raise ValueError(
                    "error_summary must be a safe single-line value up to 512 characters"
                )


def _safe_counter(name: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= _MAX_COUNTER:
        raise ValueError(f"{name} must be between 0 and {_MAX_COUNTER}")
    return value
