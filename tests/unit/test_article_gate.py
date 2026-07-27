import pytest

from commerce_agent.ingestion.article_gate import (
    ArticleGateError,
    validate_public_article,
)
from commerce_agent.ingestion.models import Platform


def article_html(text: str) -> bytes:
    paragraphs = "".join(f"<p>{text}</p>" for _ in range(20))
    return f"<html><body><article>{paragraphs}</article></body></html>".encode()


def test_public_article_accepts_complete_platform_relevant_html() -> None:
    validate_public_article(
        body=article_html(
            "Amazon marketplace sellers must review the updated compliance policy."
        ),
        content_type="text/html; charset=utf-8",
        platforms=(Platform.AMAZON,),
    )


@pytest.mark.parametrize(
    ("content_type", "body", "code"),
    [
        (
            "application/pdf",
            b"%PDF-1.7",
            "article_media_type_rejected",
        ),
        (
            "text/html",
            article_html("Amazon sellers must sign in to continue reading this page."),
            "article_access_wall",
        ),
        (
            "text/html",
            article_html("Amazon readers must verify you are human with a CAPTCHA."),
            "article_access_wall",
        ),
        (
            "text/html",
            b"<html><article><p>Amazon short update.</p></article></html>",
            "article_body_incomplete",
        ),
        (
            "text/html",
            article_html("A long report about an unrelated local sporting event."),
            "article_platform_irrelevant",
        ),
    ],
)
def test_public_article_rejects_unusable_pages(
    content_type: str,
    body: bytes,
    code: str,
) -> None:
    with pytest.raises(ArticleGateError, match=code) as error:
        validate_public_article(
            body=body,
            content_type=content_type,
            platforms=(Platform.AMAZON,),
        )

    assert error.value.code == code


@pytest.mark.parametrize(
    ("platform", "mention"),
    [
        (Platform.TEMU, "Temu seller policy"),
        (Platform.SHEIN, "SHEIN marketplace"),
        (Platform.ALIEXPRESS, "AliExpress seller rules"),
        (Platform.SHOPEE, "Shopee marketplace update"),
        (Platform.EBAY, "eBay seller update"),
        (Platform.COUPANG, "Coupang marketplace"),
        (Platform.OZON, "Ozon seller policy"),
        (Platform.JOYBUY, "Joybuy marketplace"),
        (Platform.TIKTOK_SHOP, "TikTok Shop seller policy"),
    ],
)
def test_public_article_recognizes_each_platform(
    platform: Platform,
    mention: str,
) -> None:
    validate_public_article(
        body=article_html(f"{mention} has changed for cross-border merchants."),
        content_type="application/xhtml+xml",
        platforms=(platform,),
    )


def test_article_gate_error_never_contains_page_content() -> None:
    private_marker = "private-page-token-987"

    with pytest.raises(ArticleGateError) as error:
        validate_public_article(
            body=f"<html>{private_marker}</html>".encode(),
            content_type="text/html",
            platforms=(Platform.AMAZON,),
        )

    assert private_marker not in str(error.value)
