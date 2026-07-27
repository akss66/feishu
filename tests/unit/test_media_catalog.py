from commerce_agent.media.catalog import (
    ArticleAccess,
    MediaCategory,
    publisher_name,
    publisher_profile,
    publisher_profiles,
)


def test_catalog_normalizes_www_without_suffix_confusion() -> None:
    profile = publisher_profile("www.reuters.com")

    assert profile is not None
    assert profile.publisher_key == "reuters.com"
    assert profile.display_name == "Reuters"
    assert profile.category is MediaCategory.GLOBAL_AUTHORITY
    assert profile.article_access is ArticleAccess.AUTHORIZATION_REQUIRED
    assert publisher_profile("reuters.com.example") is None


def test_catalog_includes_three_media_categories() -> None:
    assert publisher_profile("apnews.com").category is MediaCategory.GLOBAL_AUTHORITY
    assert publisher_profile("retaildive.com").category is MediaCategory.SPECIALIST
    assert publisher_profile("cifnews.com").category is MediaCategory.CHINESE_INDUSTRY


def test_catalog_includes_requested_chinese_industry_publishers() -> None:
    expected = {
        "cifnews.com": "雨果跨境",
        "ennews.com": "亿恩网",
        "chwang.com": "出海网",
        "dsb.cn": "电商报",
        "100ec.cn": "网经社跨境电商台",
    }

    for publisher_key, display_name in expected.items():
        profile = publisher_profile(publisher_key)
        assert profile is not None
        assert profile.display_name == display_name
        assert profile.category is MediaCategory.CHINESE_INDUSTRY
        assert profile.article_access is ArticleAccess.METADATA_ONLY


def test_catalog_matches_subdomains_and_rejects_unknown_hosts() -> None:
    assert publisher_profile("feeds.bbci.co.uk").publisher_key == "bbc.com"
    assert publisher_profile("news.marketplacepulse.com").article_access is ArticleAccess.DENIED
    assert publisher_profile("example.com") is None
    assert publisher_profile("") is None


def test_publisher_name_falls_back_to_key_for_unknown_publisher() -> None:
    assert publisher_name("digitalcommerce360.com") == "Digital Commerce 360"
    assert publisher_name("unknown.example") == "unknown.example"


def test_catalog_allows_reviewed_public_regulatory_publishers() -> None:
    expected = {
        "ftc.gov": ("Federal Trade Commission", ("ftc.gov",)),
        "gov.uk": ("UK Government", ("gov.uk",)),
        "european-union.europa.eu": (
            "European Union",
            ("european-union.europa.eu",),
        ),
    }

    for publisher_key, (display_name, allowed_hosts) in expected.items():
        profile = publisher_profile(publisher_key)
        assert profile is not None
        assert profile.display_name == display_name
        assert profile.category is MediaCategory.GLOBAL_AUTHORITY
        assert profile.article_access is ArticleAccess.ALLOWED_PUBLIC
        assert profile.allowed_hosts == allowed_hosts


def test_every_public_fetch_profile_has_an_exact_https_host_allowlist() -> None:
    public_profiles = tuple(
        profile
        for profile in publisher_profiles()
        if profile.article_access is ArticleAccess.ALLOWED_PUBLIC
    )

    assert public_profiles
    for profile in public_profiles:
        assert profile.allowed_hosts
        assert all(
            host == host.lower()
            and "://" not in host
            and "/" not in host
            and host.strip(".") == host
            for host in profile.allowed_hosts
        )


def test_catalog_access_decisions_match_the_compliance_review() -> None:
    expected = {
        "reuters.com": ArticleAccess.AUTHORIZATION_REQUIRED,
        "apnews.com": ArticleAccess.AUTHORIZATION_REQUIRED,
        "bloomberg.com": ArticleAccess.AUTHORIZATION_REQUIRED,
        "ft.com": ArticleAccess.AUTHORIZATION_REQUIRED,
        "cnbc.com": ArticleAccess.AUTHORIZATION_REQUIRED,
        "bbc.com": ArticleAccess.AUTHORIZATION_REQUIRED,
        "retaildive.com": ArticleAccess.AUTHORIZATION_REQUIRED,
        "digitalcommerce360.com": ArticleAccess.AUTHORIZATION_REQUIRED,
        "ecommercebytes.com": ArticleAccess.AUTHORIZATION_REQUIRED,
        "modernretail.co": ArticleAccess.AUTHORIZATION_REQUIRED,
        "marketplacepulse.com": ArticleAccess.DENIED,
        "cifnews.com": ArticleAccess.METADATA_ONLY,
        "ennews.com": ArticleAccess.METADATA_ONLY,
        "chwang.com": ArticleAccess.METADATA_ONLY,
        "dsb.cn": ArticleAccess.METADATA_ONLY,
        "100ec.cn": ArticleAccess.METADATA_ONLY,
        "ebrun.com": ArticleAccess.METADATA_ONLY,
        "baijing.cn": ArticleAccess.METADATA_ONLY,
        "36kr.com": ArticleAccess.METADATA_ONLY,
        "ftc.gov": ArticleAccess.ALLOWED_PUBLIC,
        "gov.uk": ArticleAccess.ALLOWED_PUBLIC,
        "european-union.europa.eu": ArticleAccess.ALLOWED_PUBLIC,
    }

    assert {
        profile.publisher_key: profile.article_access
        for profile in publisher_profiles()
    } == expected
