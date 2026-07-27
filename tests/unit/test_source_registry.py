from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    ContentScope,
    CoverageStatus,
    Platform,
    SourceAdapter,
    TrustTier,
)
from commerce_agent.ingestion.registry import SourceRegistry, SourceRegistryError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ingestion"
PUBLIC_SOURCES = (
    Path(__file__).parents[2] / "src" / "commerce_agent" / "sources" / "public_sources.yaml"
)
SOURCE_ACCEPTANCE = (
    Path(__file__).parents[2] / "docs" / "operations" / "ten-platform-source-acceptance.md"
)

BALANCED_REVIEW_SOURCE_IDS = {
    "amazon-seller-blog",
    "amazon-seller-announcements",
    "amazon-seller-forums",
    "shopee-sg-seller-education",
    "shopee-my-seller-education",
    "shopee-ph-seller-education",
    "ebay-press-room",
    "ebay-seller-updates",
    "coupang-rules-and-policies",
    "coupang-seller-university",
    "coupang-global-news",
    "ozon-seller-news",
    "ozon-seller-media",
    "ozon-global-docs",
    "joybuy-news",
    "joybuy-german-news",
    "joybuy-dutch-news",
}

BALANCED_REVIEW_STATUS = {
    "amazon-seller-blog": ComplianceStatus.PENDING_REVIEW,
    "amazon-seller-announcements": ComplianceStatus.PENDING_REVIEW,
    "amazon-seller-forums": ComplianceStatus.PENDING_REVIEW,
    "shopee-sg-seller-education": ComplianceStatus.AUTHORIZATION_REQUIRED,
    "shopee-my-seller-education": ComplianceStatus.AUTHORIZATION_REQUIRED,
    "shopee-ph-seller-education": ComplianceStatus.AUTHORIZATION_REQUIRED,
    "ebay-press-room": ComplianceStatus.ALLOWED,
    "ebay-seller-updates": ComplianceStatus.AUTHORIZATION_REQUIRED,
    "coupang-rules-and-policies": ComplianceStatus.PENDING_REVIEW,
    "coupang-seller-university": ComplianceStatus.ALLOWED,
    "coupang-global-news": ComplianceStatus.PENDING_REVIEW,
    "ozon-seller-news": ComplianceStatus.PENDING_REVIEW,
    "ozon-seller-media": ComplianceStatus.PENDING_REVIEW,
    "ozon-global-docs": ComplianceStatus.PENDING_REVIEW,
    "joybuy-news": ComplianceStatus.ALLOWED,
    "joybuy-german-news": ComplianceStatus.ALLOWED,
    "joybuy-dutch-news": ComplianceStatus.ALLOWED,
}

OUT_OF_SCOPE_STATUS = {
    "amazon-sp-api-changelog-rss": (ComplianceStatus.ALLOWED, True),
    "temu-seller-center": (ComplianceStatus.DENIED, False),
    "temu-about": (ComplianceStatus.DENIED, False),
    "temu-support-center": (ComplianceStatus.DENIED, False),
    "shein-group-newsroom": (ComplianceStatus.DENIED, False),
    "shein-group-press-releases": (ComplianceStatus.DENIED, False),
    "shein-group-company-updates": (ComplianceStatus.DENIED, False),
    "aliexpress-marketplace": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "aliexpress-seller-portal": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "aliexpress-terms-center": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "ebay-newsroom-rss": (ComplianceStatus.ALLOWED, True),
    "tiktok-shop-academy": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "tiktok-shop-policy-pulse": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "tiktok-shop-sg-seller-terms": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "media-digital-commerce-360-feed": (
        ComplianceStatus.AUTHORIZATION_REQUIRED,
        False,
    ),
    "media-ecommercebytes-feed": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "media-gdelt-amazon": (ComplianceStatus.ALLOWED, False),
    "media-gdelt-temu": (ComplianceStatus.ALLOWED, False),
    "media-gdelt-shein": (ComplianceStatus.ALLOWED, False),
    "media-gdelt-aliexpress": (ComplianceStatus.ALLOWED, False),
    "media-gdelt-shopee": (ComplianceStatus.ALLOWED, False),
    "media-gdelt-ebay": (ComplianceStatus.ALLOWED, False),
    "media-gdelt-coupang": (ComplianceStatus.ALLOWED, False),
    "media-gdelt-ozon": (ComplianceStatus.ALLOWED, False),
    "media-gdelt-joybuy": (ComplianceStatus.ALLOWED, False),
    "media-gdelt-tiktok-shop": (ComplianceStatus.ALLOWED, False),
    "media-marketplace-pulse": (ComplianceStatus.DENIED, False),
    "media-reuters-retail": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
}

BALANCED_REVIEW_SOURCE_EVIDENCE = {
    "amazon-seller-forums": {
        "robots_url": "https://sellercentral.amazon.com/robots.txt",
    },
    "shopee-sg-seller-education": {
        "terms_url": "https://shopee.sg/legaldoc/termsOfService/",
        "robots_url": "https://seller.shopee.sg/robots.txt",
    },
    "shopee-my-seller-education": {
        "terms_url": "https://shopee.com.my/legaldoc/termsOfService/",
        "robots_url": "https://seller.shopee.com.my/robots.txt",
        "regions": ("my",),
    },
    "shopee-ph-seller-education": {
        "terms_url": "https://shopee.ph/legaldoc/termsOfService/",
        "robots_url": "https://seller.shopee.ph/robots.txt",
        "regions": ("ph",),
    },
    "ebay-seller-updates": {
        "terms_url": "https://www.ebay.com/help/policies/member-behaviour-policies/user-agreement?id=4259",
        "robots_url": "https://www.ebay.com/robots.txt",
    },
    "joybuy-german-news": {
        "regions": ("de",),
        "language_hint": "de",
    },
    "joybuy-dutch-news": {
        "regions": ("nl",),
        "language_hint": "nl",
    },
}

BALANCED_REVIEW_NOTE_MARKERS = {
    "amazon-seller-blog": "public blog",
    "amazon-seller-announcements": "announcements list",
    "amazon-seller-forums": "Agent Policy",
    "shopee-sg-seller-education": "Singapore education hub",
    "shopee-my-seller-education": "Malaysia education hub",
    "shopee-ph-seller-education": "Philippines education hub",
    "ebay-press-room": "public press room",
    "ebay-seller-updates": "Seller News",
    "coupang-rules-and-policies": "Rules and Policies list",
    "coupang-seller-university": "Seller University",
    "coupang-global-news": "Step-by-step Guide",
    "ozon-seller-news": "fbo-i-fbs",
    "ozon-seller-media": "promokodov",
    "ozon-global-docs": "dokumenty",
    "joybuy-news": "English newsroom",
    "joybuy-german-news": "German newsroom",
    "joybuy-dutch-news": "Dutch newsroom",
}


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
    assert {item.value for item in SourceAdapter} == {"generic", "gdelt"}
    assert {item.value for item in ContentScope} == {
        "metadata_only",
        "feed_summary",
        "full_text",
    }
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
    assert registry.require("alpha-api").adapter is SourceAdapter.GENERIC
    assert registry.require("alpha-api").content_scope is ContentScope.METADATA_ONLY
    assert registry.require("alpha-api").attribution == "Alpha News"
    assert registry.require("alpha-api").publisher_key == "alpha.example"
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


@pytest.mark.parametrize("field", ["content_scope", "attribution", "publisher_key"])
def test_enabled_source_requires_complete_material_policy(
    tmp_path: Path,
    field: str,
) -> None:
    document = _valid_document()
    source = document["sources"][0]  # type: ignore[index]
    source.pop(field, None)

    with pytest.raises(SourceRegistryError, match=rf"zeta-html.*{field}"):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))


def test_disabled_unannotated_media_candidate_remains_loadable(tmp_path: Path) -> None:
    document = _valid_document()
    source = document["sources"][1]  # type: ignore[index]
    source["enabled"] = False
    for field in ("content_scope", "attribution", "publisher_key"):
        del source[field]

    loaded = SourceRegistry.from_yaml(_write_registry(tmp_path, document)).require("alpha-api")

    assert loaded.content_scope is None
    assert loaded.attribution is None
    assert loaded.publisher_key is None


def test_enabled_gdelt_media_uses_per_item_publisher_field(tmp_path: Path) -> None:
    document = _valid_document()
    source = document["sources"][1]  # type: ignore[index]
    source["adapter"] = "gdelt"
    source["attribution"] = "GDELT index; original publisher shown per item"
    del source["publisher_key"]
    source["collector_config"]["publisher_field"] = "domain"

    loaded = SourceRegistry.from_yaml(_write_registry(tmp_path, document)).require("alpha-api")

    assert loaded.adapter is SourceAdapter.GDELT
    assert loaded.publisher_key is None
    assert loaded.collector_config["publisher_field"] == "domain"


def test_enabled_gdelt_media_requires_per_item_publisher_field(tmp_path: Path) -> None:
    document = _valid_document()
    source = document["sources"][1]  # type: ignore[index]
    source["adapter"] = "gdelt"
    del source["publisher_key"]

    with pytest.raises(SourceRegistryError, match=r"alpha-api.*publisher_field"):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))


def test_rejects_unknown_adapter(tmp_path: Path) -> None:
    document = _valid_document()
    document["sources"][1]["adapter"] = "dynamic.module"  # type: ignore[index]

    with pytest.raises(SourceRegistryError, match=r"alpha-api.*adapter"):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))


def test_enabled_full_text_media_is_allowed_when_material_policy_complete(
    tmp_path: Path,
) -> None:
    document = _valid_document()
    document["sources"][1]["content_scope"] = "full_text"  # type: ignore[index]

    loaded = SourceRegistry.from_yaml(_write_registry(tmp_path, document))

    assert loaded.require("alpha-api").content_scope is ContentScope.FULL_TEXT


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


def test_each_platform_has_two_registered_candidate_publishers() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)

    for platform in Platform:
        candidates = {
            source.publisher_key
            for source in registry.sources
            if platform in source.platforms and source.publisher_key
        }
        assert len(candidates) >= 2, platform


def test_public_registry_has_one_bounded_gdelt_query_per_platform() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    gdelt = tuple(
        source
        for source in registry.sources
        if source.adapter is SourceAdapter.GDELT
    )

    assert len(gdelt) == len(Platform)
    assert {source.platforms[0] for source in gdelt} == set(Platform)
    for source in gdelt:
        assert len(source.platforms) == 1
        assert source.content_scope is ContentScope.METADATA_ONLY
        assert source.enabled is False
        assert source.collector_config["item_limit"] == 25
        query = parse_qs(urlsplit(source.entry_url).query)
        assert query["mode"] == ["artlist"]
        assert query["format"] == ["json"]
        assert query["maxrecords"] == ["25"]
        assert query["timespan"] == ["1d"]
        assert query["sort"] == ["datedesc"]


def test_public_registry_includes_reviewed_ten_platform_official_candidates() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    expected = {
        "amazon-about-small-business": Platform.AMAZON,
        "temu-press-corner": Platform.TEMU,
        "shein-group-newsroom": Platform.SHEIN,
        "alibaba-group-news": Platform.ALIEXPRESS,
        "sea-group-news": Platform.SHOPEE,
        "ebay-press-room": Platform.EBAY,
        "coupang-korean-newsroom": Platform.COUPANG,
        "ozon-investor-news": Platform.OZON,
        "jd-corporate-blog": Platform.JOYBUY,
        "tiktok-newsroom": Platform.TIKTOK_SHOP,
    }

    for source_id, platform in expected.items():
        source = registry.require(source_id)
        assert source.platforms == (platform,)
        assert source.trust_tier is TrustTier.OFFICIAL
        assert source.publisher_key


def test_source_acceptance_register_covers_new_candidate_sources() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    acceptance = SOURCE_ACCEPTANCE.read_text(encoding="utf-8")
    candidate_ids = {
        source.source_id
        for source in registry.sources
        if source.reviewed_at == date(2026, 7, 27)
    }

    assert candidate_ids
    assert all(f"## {source_id}" in acceptance for source_id in candidate_ids)
    for label in (
        "Platform:",
        "Publisher:",
        "Entry URL:",
        "Terms evidence:",
        "Robots evidence:",
        "Full-text storage permission:",
        "90-day relevance evidence:",
        "Offline fixture:",
        "Live smoke date and result:",
        "Final status:",
    ):
        assert acceptance.count(label) >= len(candidate_ids)


def test_public_registry_applies_balanced_review_decisions_without_scope_drift() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    reviewed = tuple(registry.require(source_id) for source_id in BALANCED_REVIEW_SOURCE_IDS)

    assert set(BALANCED_REVIEW_STATUS) == BALANCED_REVIEW_SOURCE_IDS
    assert {
        source.source_id: source.compliance
        for source in reviewed
    } == BALANCED_REVIEW_STATUS
    assert all(source.reviewed_at == date(2026, 7, 22) for source in reviewed)
    assert all(len(source.compliance_notes) >= 80 for source in reviewed)
    assert all(
        source.enabled == (source.compliance is ComplianceStatus.ALLOWED)
        for source in reviewed
    )
    assert all(
        source.collector_config.get("item_limit", 20) <= 20
        for source in reviewed
        if source.compliance is ComplianceStatus.ALLOWED
    )
    assert {
        source_id: (
            registry.require(source_id).compliance,
            registry.require(source_id).enabled,
        )
        for source_id in OUT_OF_SCOPE_STATUS
    } == OUT_OF_SCOPE_STATUS
    assert {
        source_id: {
            field: getattr(registry.require(source_id), field)
            for field in evidence
        }
        for source_id, evidence in BALANCED_REVIEW_SOURCE_EVIDENCE.items()
    } == BALANCED_REVIEW_SOURCE_EVIDENCE
    assert set(BALANCED_REVIEW_NOTE_MARKERS) == BALANCED_REVIEW_SOURCE_IDS
    assert all(
        marker in registry.require(source_id).compliance_notes
        for source_id, marker in BALANCED_REVIEW_NOTE_MARKERS.items()
    )


def test_public_registry_media_candidates_are_fully_annotated_but_stay_disabled() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    media = tuple(source for source in registry.sources if source.trust_tier is TrustTier.MEDIA)

    assert media
    for source in media:
        assert source.adapter in {SourceAdapter.GENERIC, SourceAdapter.GDELT}
        assert source.content_scope in {
            ContentScope.METADATA_ONLY,
            ContentScope.FEED_SUMMARY,
        }
        assert source.attribution
        if source.adapter is SourceAdapter.GENERIC:
            assert source.publisher_key
        else:
            assert source.publisher_key is None
        assert source.enabled is False


def test_requested_chinese_media_candidates_are_registered_but_disabled() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    expected = {
        "media-cifnews-cross-border": "cifnews.com",
        "media-ennews-cross-border": "ennews.com",
        "media-chwang-cross-border": "chwang.com",
        "media-dsb-cross-border": "dsb.cn",
        "media-100ec-cross-border": "100ec.cn",
    }

    for source_id, publisher_key in expected.items():
        source = registry.require(source_id)
        assert source.publisher_key == publisher_key
        assert source.trust_tier is TrustTier.MEDIA
        assert source.content_scope is ContentScope.METADATA_ONLY
        assert source.enabled is False


def test_first_live_source_definitions_match_reviewed_endpoints_and_budgets() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    amazon = registry.require("amazon-sp-api-changelog-rss")
    ebay = registry.require("ebay-newsroom-rss")
    gdelt = registry.require("media-gdelt-amazon")

    assert amazon.entry_url == "https://developer-docs.amazon/sp-api/changelog.rss"
    assert amazon.collector is CollectorKind.RSS
    assert amazon.compliance is ComplianceStatus.ALLOWED
    assert amazon.enabled is True
    assert ebay.collector is CollectorKind.RSS
    assert ebay.enabled is True
    assert gdelt.adapter is SourceAdapter.GDELT
    assert gdelt.collector is CollectorKind.API
    assert gdelt.content_scope is ContentScope.METADATA_ONLY
    assert gdelt.publisher_key is None
    assert gdelt.platforms == (Platform.AMAZON,)
    assert gdelt.enabled is False
    assert gdelt.collector_config == {
        "items_path": "$.articles",
        "url_field": "$.url",
        "title_field": "$.title",
        "published_at_field": "$.seendate",
        "publisher_field": "$.domain",
        "item_limit": 25,
    }
    query = parse_qs(urlsplit(gdelt.entry_url).query)
    assert query["mode"] == ["artlist"]
    assert query["format"] == ["json"]
    assert query["maxrecords"] == ["25"]
    assert query["timespan"] == ["1d"]
    assert query["sort"] == ["datedesc"]
    assert "Amazon marketplace" in query["query"][0]
