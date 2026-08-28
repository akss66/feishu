"""Deterministic quality gate for publicly fetched media articles."""

from __future__ import annotations

import re

import trafilatura
from lxml import html as lxml_html

from commerce_agent.ingestion.dedupe import normalize_text
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
    return bool(matched_target_platforms(text, platforms))


def matched_target_platforms(
    text: str,
    platforms: tuple[Platform, ...],
) -> tuple[Platform, ...]:
    folded = text.casefold()
    return tuple(
        platform
        for platform in platforms
        if any(_alias_matches(folded, alias) for alias in _PLATFORM_ALIASES[platform])
    )


def validate_public_article(
    *,
    body: bytes,
    content_type: str | None,
    platforms: tuple[Platform, ...],
) -> tuple[Platform, ...]:
    media_type = (content_type or "").partition(";")[0].strip().lower()
    if media_type not in _HTML_MEDIA_TYPES:
        raise ArticleGateError("article_media_type_rejected")

    visible_text = _visible_text(body)
    folded = visible_text.casefold()
    if any(marker in folded for marker in _ACCESS_WALL_MARKERS):
        raise ArticleGateError("article_access_wall")
    if any(marker in folded for marker in _RIGHTS_RESTRICTION_MARKERS):
        raise ArticleGateError("article_rights_restricted")
    article_text = extract_public_article_text(body)
    if len(article_text) < _MIN_VISIBLE_CHARACTERS:
        raise ArticleGateError("article_body_incomplete")
    matched = matched_target_platforms(article_text, platforms)
    if not matched:
        raise ArticleGateError("article_platform_irrelevant")
    return matched


def validate_extracted_article(
    text: str,
    platforms: tuple[Platform, ...],
) -> tuple[Platform, ...]:
    normalized = normalize_text(text)
    if len(normalized) < _MIN_VISIBLE_CHARACTERS:
        raise ArticleGateError("article_body_incomplete")
    matched = matched_target_platforms(normalized, platforms)
    if not matched:
        raise ArticleGateError("article_platform_irrelevant")
    return matched


def extract_public_article_text(body: bytes) -> str:
    cleaned = _without_boilerplate(body)
    try:
        root = lxml_html.fromstring(cleaned.decode("utf-8", errors="replace"))
    except (ValueError, TypeError, lxml_html.etree.ParserError):
        root = None
    if root is not None:
        article_nodes = root.xpath("//article")
        if article_nodes:
            return normalize_text("\n".join(_article_node_text(node) for node in article_nodes))
    _, text, _ = trafilatura.baseline(cleaned)
    return normalize_text(text)


def _alias_matches(folded: str, alias: str) -> bool:
    if alias.isascii() and len(alias) <= 3:
        return (
            re.search(
                rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
                folded,
            )
            is not None
        )
    return alias in folded


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


def _without_boilerplate(body: bytes) -> bytes:
    try:
        root = lxml_html.fromstring(body.decode("utf-8", errors="replace"))
    except (ValueError, TypeError, lxml_html.etree.ParserError):
        return body
    for element in root.xpath("//nav | //script | //style | //header | //footer | //aside"):
        element.drop_tree()
    return lxml_html.tostring(root, encoding="utf-8")


def _article_node_text(node: object) -> str:
    blocks = node.xpath(".//h1 | .//h2 | .//h3 | .//p | .//li | .//blockquote")
    if blocks:
        return "\n".join(block.text_content() for block in blocks)
    return node.text_content()
