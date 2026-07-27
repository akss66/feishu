"""Deterministic quality gate for publicly fetched media articles."""

from __future__ import annotations

from lxml import html as lxml_html

from commerce_agent.ingestion.models import Platform

_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_ACCESS_WALL_MARKERS = (
    "captcha",
    "verify you are human",
    "sign in to continue",
    "log in to continue",
    "subscribe to continue",
    "subscription required",
    "purchase a subscription",
    "enable javascript and cookies",
    "checking your browser",
    "challenge-platform",
    "正在进行安全检查，请稍候",
    "请输入验证码后继续",
    "请登录后继续阅读",
    "会员专享内容",
    "付费阅读后查看全文",
)
_RIGHTS_RESTRICTION_MARKERS = (
    "third-party copyright",
    "all rights reserved",
)
_PLATFORM_ALIASES: dict[Platform, tuple[str, ...]] = {
    Platform.AMAZON: ("amazon", "亚马逊"),
    Platform.TEMU: ("temu",),
    Platform.SHEIN: ("shein", "希音"),
    Platform.ALIEXPRESS: ("aliexpress", "ali express", "速卖通"),
    Platform.SHOPEE: ("shopee",),
    Platform.EBAY: ("ebay",),
    Platform.COUPANG: ("coupang", "酷澎"),
    Platform.OZON: ("ozon",),
    Platform.JOYBUY: ("joybuy", "joy buy"),
    Platform.TIKTOK_SHOP: ("tiktok shop", "tik tok shop", "tts"),
}
_MIN_VISIBLE_CHARACTERS = 300


class ArticleGateError(ValueError):
    """Stable rejection that never includes untrusted article content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"article gate rejected content: {code}")


def mentions_target_platform(
    text: str,
    platforms: tuple[Platform, ...],
) -> bool:
    folded = text.casefold()
    return any(
        alias in folded
        for platform in platforms
        for alias in _PLATFORM_ALIASES[platform]
    )


def validate_public_article(
    *,
    body: bytes,
    content_type: str | None,
    platforms: tuple[Platform, ...],
) -> None:
    media_type = (content_type or "").partition(";")[0].strip().lower()
    if media_type not in _HTML_MEDIA_TYPES:
        raise ArticleGateError("article_media_type_rejected")

    visible_text = _visible_text(body)
    folded = visible_text.casefold()
    if any(marker in folded for marker in _ACCESS_WALL_MARKERS):
        raise ArticleGateError("article_access_wall")
    if any(marker in folded for marker in _RIGHTS_RESTRICTION_MARKERS):
        raise ArticleGateError("article_rights_restricted")
    if len(visible_text) < _MIN_VISIBLE_CHARACTERS:
        raise ArticleGateError("article_body_incomplete")
    if not mentions_target_platform(visible_text, platforms):
        raise ArticleGateError("article_platform_irrelevant")


def _visible_text(body: bytes) -> str:
    try:
        document = body.decode("utf-8")
    except UnicodeDecodeError:
        document = body
    try:
        root = lxml_html.fromstring(document)
    except (ValueError, TypeError, lxml_html.etree.ParserError):
        return ""
    for element in root.xpath("//script | //style | //nav | //header | //footer | //aside"):
        element.drop_tree()
    return " ".join(root.text_content().split())
