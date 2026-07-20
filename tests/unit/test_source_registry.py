from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    CoverageStatus,
    Platform,
    TrustTier,
)
from commerce_agent.ingestion.registry import SourceRegistry, SourceRegistryError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ingestion"
PUBLIC_SOURCES = (
    Path(__file__).parents[2] / "src" / "commerce_agent" / "sources" / "public_sources.yaml"
)


def _valid_document() -> dict[str, object]:
    return yaml.safe_load((FIXTURES / "valid_sources.yaml").read_text(encoding="utf-8"))


def _write_registry(tmp_path: Path, document: dict[str, object]) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_enum_values_are_stable() -> None:
    assert tuple(Platform) == (
        Platform.AMAZON,
        Platform.TEMU,
        Platform.SHEIN,
        Platform.ALIEXPRESS,
        Platform.SHOPEE,
        Platform.EBAY,
        Platform.COUPANG,
        Platform.OZON,
        Platform.JOYBUY,
        Platform.TIKTOK_SHOP,
    )
    assert [platform.value for platform in Platform] == [
        "amazon",
        "temu",
        "shein",
        "aliexpress",
        "shopee",
        "ebay",
        "coupang",
        "ozon",
        "joybuy",
        "tiktok_shop",
    ]
    assert {item.value for item in CollectorKind} == {"rss", "sitemap", "html", "api", "browser"}
    assert {item.value for item in TrustTier} == {"official", "media"}
    assert {item.value for item in ComplianceStatus} == {
        "allowed",
        "pending_review",
        "denied",
        "authorization_required",
    }


def test_loads_valid_yaml_into_immutable_definitions_in_source_id_order() -> None:
    registry = SourceRegistry.from_yaml(FIXTURES / "valid_sources.yaml")

    assert [source.source_id for source in registry.sources] == [
        "alpha-api",
        "middle-feed",
        "zeta-html",
    ]
    assert [source.source_id for source in registry.enabled()] == ["alpha-api", "zeta-html"]
    assert registry.require("zeta-html").collector_config["link_selector"] == "article a"
    with pytest.raises(FrozenInstanceError):
        registry.require("zeta-html").enabled = False  # type: ignore[misc]
    with pytest.raises(TypeError):
        registry.require("zeta-html").collector_config["link_selector"] = "a"  # type: ignore[index]


def test_require_unknown_source_has_readable_error() -> None:
    registry = SourceRegistry.from_yaml(FIXTURES / "valid_sources.yaml")

    with pytest.raises(KeyError, match="missing-source"):
        registry.require("missing-source")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("platforms", ["not-a-platform"]),
        ("collector", "graphql"),
        ("trust_tier", "blog"),
        ("compliance", "maybe"),
    ],
)
def test_rejects_invalid_enum_values_with_source_id(
    tmp_path: Path, field: str, value: object
) -> None:
    document = _valid_document()
    document["sources"][0][field] = value  # type: ignore[index]

    with pytest.raises(SourceRegistryError, match=r"zeta-html.*" + field):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))


def test_rejects_duplicate_source_ids(tmp_path: Path) -> None:
    document = _valid_document()
    document["sources"][1]["source_id"] = "zeta-html"  # type: ignore[index]

    with pytest.raises(SourceRegistryError, match=r"zeta-html.*duplicate"):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))


@pytest.mark.parametrize("field", ["entry_url", "terms_url", "robots_url"])
def test_rejects_malformed_urls_with_source_id(tmp_path: Path, field: str) -> None:
    document = _valid_document()
    document["sources"][0][field] = "javascript:alert(1)"  # type: ignore[index]

    with pytest.raises(SourceRegistryError, match=r"zeta-html.*" + field):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))


def test_rejects_url_with_embedded_whitespace(tmp_path: Path) -> None:
    document = _valid_document()
    document["sources"][0]["entry_url"] = "https://invalid host.example/news"  # type: ignore[index]

    with pytest.raises(SourceRegistryError, match=r"zeta-html.*entry_url"):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))


@pytest.mark.parametrize("field", ["entry_url", "terms_url", "robots_url"])
@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "[::1]",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "[fe80::1]",
        "0.0.0.0",
        "[::]",
        "240.0.0.1",
        "192.0.2.1",
        "169.254.169.254",
        "100.100.100.200",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data.ec2.internal",
    ],
)
def test_rejects_non_public_and_metadata_hosts(tmp_path: Path, host: str, field: str) -> None:
    document = _valid_document()
    document["sources"][0][field] = f"http://{host}/news"  # type: ignore[index]

    with pytest.raises(SourceRegistryError, match=rf"zeta-html.*{field}"):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))


@pytest.mark.parametrize(
    "field",
    ["terms_url", "robots_url", "reviewed_at", "compliance_notes"],
)
def test_rejects_missing_compliance_evidence(tmp_path: Path, field: str) -> None:
    document = _valid_document()
    del document["sources"][0][field]  # type: ignore[index]

    with pytest.raises(SourceRegistryError, match=r"zeta-html.*" + field):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))


def test_rejects_enabled_source_that_is_not_allowed() -> None:
    with pytest.raises(SourceRegistryError, match=r"invalid-enabled-source.*allowed"):
        SourceRegistry.from_yaml(FIXTURES / "invalid_sources.yaml")


@pytest.mark.parametrize(
    ("collector", "config", "expected_field"),
    [
        ("html", {"article_selector": "article"}, "link_selector"),
        ("browser", {}, "link_selector"),
        ("api", {"items_path": "items"}, "url_field"),
        ("rss", {"link_selector": "a"}, "link_selector"),
    ],
)
def test_validates_collector_specific_fields(
    tmp_path: Path,
    collector: str,
    config: dict[str, object],
    expected_field: str,
) -> None:
    document = _valid_document()
    source = document["sources"][0]  # type: ignore[index]
    source["collector"] = collector
    source["collector_config"] = config

    with pytest.raises(SourceRegistryError, match=rf"zeta-html.*{expected_field}"):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))


def test_rejects_unknown_keys_with_source_id(tmp_path: Path) -> None:
    document = _valid_document()
    document["sources"][0]["secret_header"] = "do-not-accept"  # type: ignore[index]

    with pytest.raises(SourceRegistryError, match=r"zeta-html.*secret_header"):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))


def test_platform_coverage_is_deterministic_and_includes_pending_official_sources() -> None:
    registry = SourceRegistry.from_yaml(FIXTURES / "valid_sources.yaml")

    coverage = registry.platform_coverage()

    assert list(coverage) == list(Platform)
    assert coverage[Platform.AMAZON] is CoverageStatus.OFFICIAL_PUBLIC_COVERED
    assert coverage[Platform.EBAY] is CoverageStatus.PARTIAL
    assert coverage[Platform.TEMU] is CoverageStatus.PARTIAL
    assert coverage[Platform.SHOPEE] is CoverageStatus.PUBLIC_COVERED_SELLER_CENTER_PENDING
    assert coverage[Platform.OZON] is CoverageStatus.UNCONNECTED


def test_public_registry_has_required_platform_coverage_and_seed_mix() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)

    assert set(registry.platform_coverage()) == set(Platform)
    assert len(registry.sources) >= 30
    assert {source.trust_tier for source in registry.sources} == {
        TrustTier.OFFICIAL,
        TrustTier.MEDIA,
    }
    for platform in Platform:
        assert any(
            platform in source.platforms
            and (
                source.trust_tier is TrustTier.OFFICIAL
                or source.compliance is ComplianceStatus.AUTHORIZATION_REQUIRED
            )
            for source in registry.sources
        ), platform
