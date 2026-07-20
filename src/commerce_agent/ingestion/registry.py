from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar
from urllib.parse import urlsplit

import yaml

from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    CoverageStatus,
    Platform,
    Scalar,
    SourceDefinition,
    TrustTier,
)


class SourceRegistryError(ValueError):
    """Raised when a source registry violates its versioned contract."""


_EnumT = TypeVar("_EnumT", Platform, CollectorKind, TrustTier, ComplianceStatus)
_SOURCE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ROOT_FIELDS = frozenset({"version", "sources"})
_SOURCE_FIELDS = frozenset(
    {
        "source_id",
        "name",
        "entry_url",
        "platforms",
        "trust_tier",
        "collector",
        "compliance",
        "enabled",
        "regions",
        "language_hint",
        "interval_minutes",
        "terms_url",
        "robots_url",
        "reviewed_at",
        "compliance_notes",
        "collector_config",
    }
)
_REQUIRED_SOURCE_FIELDS = _SOURCE_FIELDS - {"language_hint", "collector_config"}
_CONFIG_FIELDS: dict[CollectorKind, frozenset[str]] = {
    CollectorKind.RSS: frozenset({"item_limit"}),
    CollectorKind.SITEMAP: frozenset({"item_limit"}),
    CollectorKind.HTML: frozenset({"link_selector", "article_selector", "item_limit"}),
    CollectorKind.API: frozenset(
        {"items_path", "url_field", "title_field", "published_at_field", "item_limit"}
    ),
    CollectorKind.BROWSER: frozenset({"link_selector", "article_selector", "item_limit"}),
}
_REQUIRED_CONFIG_FIELDS: dict[CollectorKind, frozenset[str]] = {
    CollectorKind.RSS: frozenset(),
    CollectorKind.SITEMAP: frozenset(),
    CollectorKind.HTML: frozenset({"link_selector"}),
    CollectorKind.API: frozenset({"items_path", "url_field"}),
    CollectorKind.BROWSER: frozenset({"link_selector"}),
}


class SourceRegistry:
    def __init__(self, sources: Iterable[SourceDefinition]) -> None:
        ordered = tuple(sorted(sources, key=lambda source: source.source_id))
        by_id: dict[str, SourceDefinition] = {}
        for source in ordered:
            if source.source_id in by_id:
                raise SourceRegistryError(f"source '{source.source_id}': duplicate source_id")
            by_id[source.source_id] = source
        self._sources = ordered
        self._by_id = MappingProxyType(by_id)

    @property
    def sources(self) -> tuple[SourceDefinition, ...]:
        return self._sources

    @classmethod
    def from_yaml(cls, path: Path) -> SourceRegistry:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise SourceRegistryError(f"registry '{path}': invalid YAML: {exc}") from exc

        root = _require_mapping(document, "registry")
        _reject_unknown_keys(root, _ROOT_FIELDS, "registry")
        if root.get("version") != 1 or isinstance(root.get("version"), bool):
            raise SourceRegistryError("registry: version must be integer 1")
        raw_sources = root.get("sources")
        if not isinstance(raw_sources, list):
            raise SourceRegistryError("registry: sources must be a list")

        definitions: list[SourceDefinition] = []
        seen: set[str] = set()
        for index, raw_source in enumerate(raw_sources):
            source = _parse_source(raw_source, index)
            if source.source_id in seen:
                raise SourceRegistryError(f"source '{source.source_id}': duplicate source_id")
            seen.add(source.source_id)
            definitions.append(source)
        return cls(definitions)

    def require(self, source_id: str) -> SourceDefinition:
        try:
            return self._by_id[source_id]
        except KeyError:
            raise KeyError(f"unknown source_id '{source_id}'") from None

    def enabled(self) -> tuple[SourceDefinition, ...]:
        return tuple(source for source in self._sources if source.enabled)

    def platform_coverage(self) -> dict[Platform, CoverageStatus]:
        coverage: dict[Platform, CoverageStatus] = {}
        for platform in Platform:
            candidates = tuple(source for source in self._sources if platform in source.platforms)
            if any(
                source.enabled
                and source.compliance is ComplianceStatus.ALLOWED
                and source.trust_tier is TrustTier.OFFICIAL
                for source in candidates
            ):
                status = CoverageStatus.OFFICIAL_PUBLIC_COVERED
            elif any(
                source.trust_tier is TrustTier.OFFICIAL
                and source.compliance
                in {
                    ComplianceStatus.PENDING_REVIEW,
                    ComplianceStatus.AUTHORIZATION_REQUIRED,
                }
                for source in candidates
            ):
                status = CoverageStatus.PUBLIC_COVERED_SELLER_CENTER_PENDING
            elif any(
                source.enabled and source.compliance is ComplianceStatus.ALLOWED
                for source in candidates
            ):
                status = CoverageStatus.PARTIAL
            else:
                status = CoverageStatus.UNCONNECTED
            coverage[platform] = status
        return coverage


def _parse_source(raw_source: object, index: int) -> SourceDefinition:
    source = _require_mapping(raw_source, f"source entry {index}")
    raw_id = source.get("source_id")
    source_id = raw_id if isinstance(raw_id, str) and raw_id else f"entry-{index}"
    context = f"source '{source_id}'"
    _reject_unknown_keys(source, _SOURCE_FIELDS, context)
    missing = sorted(_REQUIRED_SOURCE_FIELDS - source.keys())
    if missing:
        raise SourceRegistryError(f"{context}: missing required field '{missing[0]}'")
    if not isinstance(raw_id, str) or not _SOURCE_ID.fullmatch(raw_id):
        raise SourceRegistryError(
            f"{context}: source_id must contain lowercase letters, digits, and hyphens"
        )

    collector = _parse_enum(CollectorKind, source["collector"], "collector", context)
    compliance = _parse_enum(ComplianceStatus, source["compliance"], "compliance", context)
    enabled = _require_bool(source["enabled"], "enabled", context)
    if enabled and compliance is not ComplianceStatus.ALLOWED:
        raise SourceRegistryError(
            f"{context}: enabled sources must have compliance status 'allowed'"
        )

    collector_config = _parse_collector_config(
        source.get("collector_config", {}), collector, context
    )
    reviewed_at = _parse_date(source["reviewed_at"], "reviewed_at", context)
    language_hint = source.get("language_hint")
    if language_hint is not None and (
        not isinstance(language_hint, str) or not language_hint.strip()
    ):
        raise SourceRegistryError(f"{context}: language_hint must be a non-empty string or null")

    return SourceDefinition(
        source_id=raw_id,
        name=_require_nonempty_string(source["name"], "name", context),
        entry_url=_require_url(source["entry_url"], "entry_url", context),
        platforms=_parse_platforms(source["platforms"], context),
        trust_tier=_parse_enum(TrustTier, source["trust_tier"], "trust_tier", context),
        collector=collector,
        compliance=compliance,
        enabled=enabled,
        regions=_parse_string_list(source["regions"], "regions", context),
        language_hint=language_hint.strip() if isinstance(language_hint, str) else None,
        interval_minutes=_require_positive_int(
            source["interval_minutes"], "interval_minutes", context
        ),
        terms_url=_require_url(source["terms_url"], "terms_url", context),
        robots_url=_require_url(source["robots_url"], "robots_url", context),
        reviewed_at=reviewed_at,
        compliance_notes=_require_nonempty_string(
            source["compliance_notes"], "compliance_notes", context
        ),
        collector_config=collector_config,
    )


def _require_mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SourceRegistryError(f"{context}: expected a mapping with string keys")
    return value


def _reject_unknown_keys(
    mapping: Mapping[str, object], allowed: frozenset[str], context: str
) -> None:
    unknown = sorted(mapping.keys() - allowed)
    if unknown:
        raise SourceRegistryError(f"{context}: unknown field '{unknown[0]}'")


def _parse_enum(enum_type: type[_EnumT], value: object, field: str, context: str) -> _EnumT:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        allowed = ", ".join(item.value for item in enum_type)
        raise SourceRegistryError(f"{context}: {field} must be one of: {allowed}") from None


def _require_nonempty_string(value: object, field: str, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceRegistryError(f"{context}: {field} must be a non-empty string")
    return value.strip()


def _require_bool(value: object, field: str, context: str) -> bool:
    if not isinstance(value, bool):
        raise SourceRegistryError(f"{context}: {field} must be a boolean")
    return value


def _require_positive_int(value: object, field: str, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SourceRegistryError(f"{context}: {field} must be a positive integer")
    return value


def _require_url(value: object, field: str, context: str) -> str:
    url = _require_nonempty_string(value, field, context)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise SourceRegistryError(f"{context}: {field} is malformed: {exc}") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or any(character.isspace() for character in url)
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        and not (1 <= port <= 65535)
    ):
        raise SourceRegistryError(f"{context}: {field} must be an absolute HTTP(S) URL")
    return url


def _parse_platforms(value: object, context: str) -> tuple[Platform, ...]:
    if not isinstance(value, list) or not value:
        raise SourceRegistryError(f"{context}: platforms must be a non-empty list")
    platforms = tuple(_parse_enum(Platform, item, "platforms", context) for item in value)
    if len(set(platforms)) != len(platforms):
        raise SourceRegistryError(f"{context}: platforms contains duplicates")
    return tuple(sorted(platforms, key=lambda platform: list(Platform).index(platform)))


def _parse_string_list(value: object, field: str, context: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise SourceRegistryError(f"{context}: {field} must be a non-empty list of strings")
    normalized = tuple(item.strip() for item in value)
    if len(set(normalized)) != len(normalized):
        raise SourceRegistryError(f"{context}: {field} contains duplicates")
    return normalized


def _parse_date(value: object, field: str, context: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise SourceRegistryError(f"{context}: {field} must be an ISO date (YYYY-MM-DD)")


def _parse_collector_config(
    value: object, collector: CollectorKind, context: str
) -> Mapping[str, Scalar]:
    config = _require_mapping(value, f"{context}: collector_config")
    _reject_unknown_keys(config, _CONFIG_FIELDS[collector], f"{context}: collector_config")
    missing = sorted(_REQUIRED_CONFIG_FIELDS[collector] - config.keys())
    if missing:
        raise SourceRegistryError(
            f"{context}: collector_config missing required field '{missing[0]}'"
        )
    parsed: dict[str, Scalar] = {}
    for key, item in config.items():
        if key == "item_limit":
            parsed[key] = _require_positive_int(item, key, context)
        elif not isinstance(item, str) or not item.strip():
            raise SourceRegistryError(
                f"{context}: collector_config field '{key}' must be a non-empty string"
            )
        else:
            parsed[key] = item.strip()
    return parsed
