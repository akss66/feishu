"""Immutable publisher catalog used to gate media article access."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MediaCategory(StrEnum):
    GLOBAL_AUTHORITY = "global_authority"
    SPECIALIST = "specialist"
    CHINESE_INDUSTRY = "chinese_industry"
    PUBLIC_AUTHORITY = "public_authority"


class ArticleAccess(StrEnum):
    ALLOWED_PUBLIC = "allowed_public"
    LICENSED_API = "licensed_api"
    AUTHORIZATION_REQUIRED = "authorization_required"
    METADATA_ONLY = "metadata_only"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class PublisherProfile:
    publisher_key: str
    display_name: str
    category: MediaCategory
    article_access: ArticleAccess
    allowed_hosts: tuple[str, ...]


_AUTHORITY = MediaCategory.GLOBAL_AUTHORITY
_SPECIALIST = MediaCategory.SPECIALIST
_CHINESE = MediaCategory.CHINESE_INDUSTRY
_PUBLIC_AUTHORITY = MediaCategory.PUBLIC_AUTHORITY
_AUTHORIZATION = ArticleAccess.AUTHORIZATION_REQUIRED
_METADATA_ONLY = ArticleAccess.METADATA_ONLY
_ALLOWED_PUBLIC = ArticleAccess.ALLOWED_PUBLIC

_PUBLISHERS = (
    PublisherProfile("reuters.com", "Reuters", _AUTHORITY, _AUTHORIZATION, ("reuters.com",)),
    PublisherProfile("apnews.com", "Associated Press", _AUTHORITY, _AUTHORIZATION, ("apnews.com",)),
    PublisherProfile("bloomberg.com", "Bloomberg", _AUTHORITY, _AUTHORIZATION, ("bloomberg.com",)),
    PublisherProfile("ft.com", "Financial Times", _AUTHORITY, _AUTHORIZATION, ("ft.com",)),
    PublisherProfile("cnbc.com", "CNBC", _AUTHORITY, _AUTHORIZATION, ("cnbc.com",)),
    PublisherProfile(
        "bbc.com",
        "BBC",
        _AUTHORITY,
        _AUTHORIZATION,
        ("bbc.com", "bbc.co.uk", "bbci.co.uk"),
    ),
    PublisherProfile(
        "retaildive.com",
        "Retail Dive",
        _SPECIALIST,
        _AUTHORIZATION,
        ("retaildive.com",),
    ),
    PublisherProfile(
        "digitalcommerce360.com",
        "Digital Commerce 360",
        _SPECIALIST,
        _AUTHORIZATION,
        ("digitalcommerce360.com",),
    ),
    PublisherProfile(
        "ecommercebytes.com",
        "EcommerceBytes",
        _SPECIALIST,
        _AUTHORIZATION,
        ("ecommercebytes.com",),
    ),
    PublisherProfile(
        "modernretail.co",
        "Modern Retail",
        _SPECIALIST,
        _AUTHORIZATION,
        ("modernretail.co",),
    ),
    PublisherProfile(
        "marketplacepulse.com",
        "Marketplace Pulse",
        _SPECIALIST,
        ArticleAccess.DENIED,
        ("marketplacepulse.com",),
    ),
    PublisherProfile("cifnews.com", "雨果跨境", _CHINESE, _METADATA_ONLY, ("cifnews.com",)),
    PublisherProfile("ennews.com", "亿恩网", _CHINESE, _METADATA_ONLY, ("ennews.com",)),
    PublisherProfile("chwang.com", "出海网", _CHINESE, _METADATA_ONLY, ("chwang.com",)),
    PublisherProfile("dsb.cn", "电商报", _CHINESE, _METADATA_ONLY, ("dsb.cn",)),
    PublisherProfile(
        "100ec.cn",
        "网经社跨境电商台",
        _CHINESE,
        _METADATA_ONLY,
        ("100ec.cn",),
    ),
    PublisherProfile("ebrun.com", "亿邦动力", _CHINESE, _METADATA_ONLY, ("ebrun.com",)),
    PublisherProfile("baijing.cn", "白鲸出海", _CHINESE, _METADATA_ONLY, ("baijing.cn",)),
    PublisherProfile("36kr.com", "36氪", _CHINESE, _METADATA_ONLY, ("36kr.com",)),
    PublisherProfile(
        "ftc.gov",
        "Federal Trade Commission",
        _PUBLIC_AUTHORITY,
        _ALLOWED_PUBLIC,
        ("ftc.gov", "www.ftc.gov"),
    ),
    PublisherProfile(
        "gov.uk",
        "UK Government",
        _PUBLIC_AUTHORITY,
        _ALLOWED_PUBLIC,
        ("gov.uk", "www.gov.uk"),
    ),
    PublisherProfile(
        "european-union.europa.eu",
        "European Union",
        _PUBLIC_AUTHORITY,
        _ALLOWED_PUBLIC,
        ("european-union.europa.eu",),
    ),
)
_BY_KEY = {profile.publisher_key: profile for profile in _PUBLISHERS}


def publisher_profiles() -> tuple[PublisherProfile, ...]:
    """Return the immutable, reviewed publisher profiles."""

    return _PUBLISHERS


def publisher_profile(hostname: str) -> PublisherProfile | None:
    """Resolve an exact publisher domain or one of its subdomains."""

    normalized = hostname.strip().lower().rstrip(".")
    if not normalized:
        return None
    for profile in _PUBLISHERS:
        if any(
            normalized == host or normalized.endswith(f".{host}") for host in profile.allowed_hosts
        ):
            return profile
    return None


def publisher_name(publisher_key: str) -> str:
    """Return a display name without hiding unknown publisher identities."""

    profile = _BY_KEY.get(publisher_key.strip().lower().rstrip("."))
    return profile.display_name if profile is not None else publisher_key
