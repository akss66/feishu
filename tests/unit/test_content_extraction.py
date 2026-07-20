from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from commerce_agent.ingestion.extract import (
    ContentExtractor,
    ExtractionError,
    LanguageDetection,
    LinguaLanguageDetector,
    normalize_text,
)
from commerce_agent.ingestion.models import (
    CollectedItem,
    CollectorKind,
    ComplianceStatus,
    Platform,
    SourceDefinition,
    TrustTier,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ingestion"
FETCHED_AT = datetime(2026, 7, 20, 9, tzinfo=UTC)


class FixedLanguageDetector:
    def __init__(self, language: str = "en", confidence: float = 0.98) -> None:
        self.result = LanguageDetection(language, confidence)
        self.seen: list[str] = []

    def detect(self, text: str) -> LanguageDetection:
        self.seen.append(text)
        return self.result


def source(*, article_selector: str | None = None) -> SourceDefinition:
    config: dict[str, str | int] = {"link_selector": "article a", "item_limit": 25}
    if article_selector is not None:
        config["article_selector"] = article_selector
    return SourceDefinition(
        source_id="fixture-news",
        name="Fixture newsroom",
        entry_url="https://example.com/news",
        platforms=(Platform.AMAZON,),
        trust_tier=TrustTier.OFFICIAL,
        collector=CollectorKind.HTML,
        compliance=ComplianceStatus.ALLOWED,
        enabled=True,
        regions=("global",),
        language_hint=None,
        interval_minutes=120,
        terms_url="https://example.com/terms",
        robots_url="https://example.com/robots.txt",
        reviewed_at=date(2026, 7, 20),
        compliance_notes="Offline fixture.",
        collector_config=config,
    )


def extract_fixture(name: str, *, detector=None):
    item = CollectedItem(
        url=f"https://example.com/{name.removesuffix('.html')}",
        body=(FIXTURES / name).read_bytes(),
        content_type="text/html; charset=utf-8",
    )
    return ContentExtractor(detector or LinguaLanguageDetector()).extract(
        source(), item, fetched_at=FETCHED_AT
    )


def test_trafilatura_removes_html_boilerplate_and_extracts_metadata() -> None:
    document = extract_fixture("article_en.html")

    assert "Cross-border sellers must review" in document.body
    assert "fulfillment charges" in document.body
    assert "navigation and account sign-in" not in document.body
    assert "Subscribe to marketing emails" not in document.body
    assert "Privacy | Cookies" not in document.body
    assert document.title == "Marketplace fee policy update"
    assert document.author == "Policy Team"
    assert document.published_at == datetime(2026, 7, 19, 8, 30, tzinfo=UTC)


def test_source_article_selector_overrides_automatic_extraction() -> None:
    html = b"""
    <html><head><title>Selector fixture</title></head><body>
      <article><p>This long decoy article is repeated to attract automatic extraction. """ + (
        b"decoy text " * 80
    ) + b"""</p></article>
      <section id="policy"><h1>Required notice</h1>
        <p>Only this seller policy text may be extracted.</p></section>
    </body></html>
    """
    item = CollectedItem(
        url="https://example.com/selector",
        body=html,
        content_type="text/html",
    )

    document = ContentExtractor(FixedLanguageDetector()).extract(
        source(article_selector="section#policy"), item, fetched_at=FETCHED_AT
    )

    assert document.body == "Required notice\nOnly this seller policy text may be extracted."
    assert "decoy" not in document.body


def test_feed_provided_plain_text_and_collected_metadata_take_precedence() -> None:
    published = datetime(2026, 7, 18, 6, tzinfo=UTC)
    item = CollectedItem(
        url="https://example.com/feed-entry",
        body=b"Feed supplied policy text.\r\n\r\nSecond paragraph.",
        content_type="text/plain; charset=utf-8",
        title="Feed title",
        author="Feed author",
        published_at=published,
    )

    document = ContentExtractor(FixedLanguageDetector()).extract(
        source(), item, fetched_at=FETCHED_AT
    )

    assert document.body == "Feed supplied policy text.\n\nSecond paragraph."
    assert document.title == "Feed title"
    assert document.author == "Feed author"
    assert document.published_at == published


def test_blank_content_is_rejected() -> None:
    item = CollectedItem(
        url="https://example.com/blank",
        body=b"<html><body><nav>Home</nav><script>ignore()</script></body></html>",
        content_type="text/html",
    )

    with pytest.raises(ExtractionError, match="blank_content"):
        ContentExtractor(FixedLanguageDetector()).extract(
            source(), item, fetched_at=FETCHED_AT
        )


def test_bad_published_time_does_not_crash_extraction() -> None:
    html = b"""
    <html><head><title>Safe date</title>
      <meta property="article:published_time" content="not-a-date">
    </head><body><article>
      <p>A sufficiently detailed policy body remains usable.</p>
    </article></body>
    </html>
    """
    item = CollectedItem(
        url="https://example.com/bad-date", body=html, content_type="text/html"
    )

    document = ContentExtractor(FixedLanguageDetector()).extract(
        source(), item, fetched_at=FETCHED_AT
    )

    assert document.published_at is None


def test_text_normalization_uses_nfc_and_stable_whitespace() -> None:
    assert normalize_text("  Cafe\u0301\u00a0 policy \r\n\r\n\r\n Second\tline  ") == (
        "Caf\u00e9 policy\n\nSecond line"
    )


@pytest.mark.parametrize(
    ("fixture", "expected_language", "original_fragment"),
    [
        ("article_en.html", "en", "Cross-border sellers"),
        ("article_zh.html", "zh", "跨境电商卖家需要查看最新的平台费用政策"),
        ("article_ru.html", "ru", "Продавцам электронной коммерции"),
    ],
)
def test_lingua_detects_supported_languages_and_preserves_original_text(
    fixture: str, expected_language: str, original_fragment: str
) -> None:
    document = extract_fixture(fixture)

    assert document.language == expected_language
    assert document.language_confidence >= 0.75
    assert original_fragment in document.body


@pytest.mark.parametrize("text", ["OK", "12345", "market товар platform продажа"])
def test_lingua_returns_und_for_short_or_ambiguous_text(text: str) -> None:
    detection = LinguaLanguageDetector().detect(text)

    assert detection.language == "und"
    assert 0.0 <= detection.confidence < 0.75


def test_language_detector_is_injected_and_receives_normalized_original() -> None:
    detector = FixedLanguageDetector(language="ru", confidence=0.91)
    item = CollectedItem(
        url="https://example.com/injected",
        body="  Оригинал\u00a0 текста  ".encode(),
        content_type="text/plain",
        title="Original",
    )

    document = ContentExtractor(detector).extract(source(), item, fetched_at=FETCHED_AT)

    assert detector.seen == ["Оригинал текста"]
    assert (document.language, document.language_confidence) == ("ru", 0.91)
    assert document.body == "Оригинал текста"
