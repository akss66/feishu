from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

import trafilatura
from lingua import Language, LanguageDetectorBuilder
from lxml import html as lxml_html

from commerce_agent.ingestion.dedupe import canonicalize_url, normalize_text
from commerce_agent.ingestion.models import CollectedItem, ExtractedDocument, SourceDefinition

_SUPPORTED_LANGUAGES = (Language.CHINESE, Language.ENGLISH, Language.RUSSIAN)
_SELECTOR_PART = re.compile(r"^(?P<tag>[a-zA-Z][\w-]*|\*)?(?P<suffix>(?:[.#][\w-]+)*)$")
_SELECTOR_SUFFIX = re.compile(r"([.#])([\w-]+)")
_SCRIPT_PATTERNS = (
    re.compile(r"[A-Za-z]"),
    re.compile(r"[\u0400-\u04ff]"),
    re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]"),
)


class ExtractionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"content extraction failed: {code}")


@dataclass(frozen=True, slots=True)
class LanguageDetection:
    language: str
    confidence: float


class LanguageDetector(Protocol):
    def detect(self, text: str) -> LanguageDetection: ...


class LinguaLanguageDetector:
    def __init__(
        self,
        *,
        minimum_confidence: float = 0.75,
        minimum_letters: int = 20,
    ) -> None:
        if not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between zero and one")
        if minimum_letters < 1:
            raise ValueError("minimum_letters must be positive")
        self._minimum_confidence = minimum_confidence
        self._minimum_letters = minimum_letters
        self._detector = LanguageDetectorBuilder.from_languages(*_SUPPORTED_LANGUAGES).build()

    def detect(self, text: str) -> LanguageDetection:
        letter_count = sum(character.isalpha() for character in text)
        if letter_count < self._minimum_letters or _has_mixed_supported_scripts(text):
            return LanguageDetection("und", 0.0)

        confidences = self._detector.compute_language_confidence_values(text)
        if not confidences:
            return LanguageDetection("und", 0.0)
        top = confidences[0]
        confidence = float(top.value)
        if confidence < self._minimum_confidence:
            return LanguageDetection("und", confidence)
        code = top.language.iso_code_639_1.name.lower()
        return LanguageDetection(code, confidence)


class ContentExtractor:
    def __init__(self, language_detector: LanguageDetector) -> None:
        self._language_detector = language_detector

    def extract(
        self,
        source: SourceDefinition,
        item: CollectedItem,
        *,
        fetched_at: datetime,
    ) -> ExtractedDocument:
        html_content = _is_html(item)
        raw_html = item.body if html_content else None
        metadata = _metadata_from_html(raw_html) if raw_html is not None else _HtmlMetadata()

        if html_content:
            selector = source.collector_config.get("article_selector")
            if isinstance(selector, str):
                body = _selected_text(item.body, selector)
            else:
                body = _trafilatura_text(item.body)
        else:
            body = _decode_text(item.body, item.content_type)
        body = normalize_text(body)
        if not body:
            raise ExtractionError("blank_content")

        detection = self._language_detector.detect(body)
        title = normalize_text(item.title or metadata.title or "")
        if not title:
            title = canonicalize_url(item.url)
        author = normalize_text(item.author or metadata.author or "") or None
        published_at = item.published_at or _parse_timestamp(metadata.published_at)
        return ExtractedDocument(
            source_id=source.source_id,
            canonical_url=canonicalize_url(item.url),
            title=title,
            body=body,
            language=detection.language,
            language_confidence=detection.confidence,
            fetched_at=fetched_at,
            author=author,
            published_at=published_at,
        )


@dataclass(frozen=True, slots=True)
class _HtmlMetadata:
    title: str | None = None
    author: str | None = None
    published_at: str | None = None


def _is_html(item: CollectedItem) -> bool:
    content_type = (item.content_type or "").lower()
    if "html" in content_type:
        return True
    if content_type.startswith("text/plain"):
        return False
    return item.body.lstrip().startswith((b"<html", b"<!doctype html", b"<article"))


def _decode_text(body: bytes, content_type: str | None) -> str:
    charset_match = re.search(r"charset\s*=\s*['\"]?([^;\s'\"]+)", content_type or "", re.I)
    charset = charset_match.group(1) if charset_match is not None else "utf-8"
    try:
        return body.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def _trafilatura_text(body: bytes) -> str:
    cleaned = _without_boilerplate(body)
    _, text, _ = trafilatura.baseline(cleaned)
    return text


def _without_boilerplate(body: bytes) -> bytes:
    try:
        root = lxml_html.fromstring(body)
    except (ValueError, TypeError, lxml_html.etree.ParserError):
        return body
    for element in root.xpath("//nav | //script | //style | //header | //footer | //aside"):
        element.drop_tree()
    return lxml_html.tostring(root, encoding="utf-8")


def _selected_text(body: bytes, selector: str) -> str:
    try:
        root = lxml_html.fromstring(body)
        xpath = _selector_xpath(selector)
        matches = root.xpath(xpath)
    except (ValueError, TypeError, lxml_html.etree.ParserError) as exc:
        raise ExtractionError("invalid_selector") from exc
    return "\n".join(element.text_content() for element in matches)


def _selector_xpath(selector: str) -> str:
    raw_parts = selector.split()
    if not raw_parts:
        raise ValueError("selector is empty")
    xpath_parts: list[str] = []
    for raw in raw_parts:
        match = _SELECTOR_PART.fullmatch(raw)
        if match is None:
            raise ValueError("selector is unsupported")
        tag = match.group("tag") or "*"
        predicates: list[str] = []
        element_id: str | None = None
        for prefix, value in _SELECTOR_SUFFIX.findall(match.group("suffix")):
            if prefix == "#":
                if element_id is not None:
                    raise ValueError("selector contains multiple ids")
                element_id = value
                predicates.append(f"@id={value!r}")
            else:
                predicates.append(
                    "contains(concat(' ', normalize-space(@class), ' '), "
                    f"' {value} ')"
                )
        suffix = f"[{' and '.join(predicates)}]" if predicates else ""
        xpath_parts.append(f"{tag}{suffix}")
    return "//" + "//".join(xpath_parts)


def _metadata_from_html(body: bytes) -> _HtmlMetadata:
    try:
        root = lxml_html.fromstring(body)
    except (ValueError, TypeError, lxml_html.etree.ParserError):
        return _HtmlMetadata()
    title = _first_text(root.xpath("//head/title/text()"))
    author = _first_text(
        root.xpath(
            "//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
            "'abcdefghijklmnopqrstuvwxyz')='author']/@content"
        )
    )
    published = _first_text(
        root.xpath(
            "//meta[translate(@property, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
            "'abcdefghijklmnopqrstuvwxyz')='article:published_time']/@content | "
            "//time[@datetime][1]/@datetime"
        )
    )
    return _HtmlMetadata(title=title, author=author, published_at=published)


def _first_text(values: list[object]) -> str | None:
    for value in values:
        text = normalize_text(str(value))
        if text:
            return text
    return None


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
        except (OverflowError, TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _has_mixed_supported_scripts(text: str) -> bool:
    normalized = unicodedata.normalize("NFC", text)
    counts = [len(pattern.findall(normalized)) for pattern in _SCRIPT_PATTERNS]
    supported = sum(counts)
    if supported == 0:
        return False
    material_scripts = sum(count / supported >= 0.2 for count in counts)
    return material_scripts > 1
