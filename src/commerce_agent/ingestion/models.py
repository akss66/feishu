from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType

Scalar = str | int | float | bool | None
_SAFE_ARTIFACT_HEADERS = frozenset({"content-type", "etag", "last-modified"})


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


@dataclass(frozen=True, slots=True)
class FetchContext:
    trigger: Trigger
    started_at: datetime
    etag: str | None = None
    last_modified: str | None = None


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
class CollectedItem:
    url: str
    body: bytes
    content_type: str | None = None
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
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
