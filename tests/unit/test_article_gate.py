import pytest

from commerce_agent.ingestion.article_gate import (
    ArticleGateError,
    mentions_target_platform,
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
        (
            "text/html",
            article_html(
                "Amazon policy report. This page contains third-party copyright material."
            ),
            "article_rights_restricted",
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


@pytest.mark.parametrize(
    ("platform", "mention"),
    [
        (Platform.AMAZON, "浜氶┈閫婂崠瀹舵斂绛?"),
        (Platform.SHEIN, "甯岄煶骞冲彴瑙勫垯"),
        (Platform.ALIEXPRESS, "閫熷崠閫氬悎瑙勬洿鏂?"),
        (Platform.COUPANG, "閰锋編璺ㄥ鍗栧"),
        (Platform.TIKTOK_SHOP, "TTS 搴楅摵娌荤悊"),
    ],
)
def test_article_gate_recognizes_controlled_chinese_platform_aliases(
    platform: Platform,
    mention: str,
) -> None:
    validate_public_article(
        body=article_html(f"{mention}鍙戠敓鍙樺寲锛屽晢瀹堕渶瑕佹牳鏌ュ晢鍝佸拰璐︽埛銆?"),
        content_type="text/html",
        platforms=(platform,),
    )


def test_title_prefilter_uses_the_same_platform_aliases_as_body_gate() -> None:
    assert mentions_target_platform(
        "浜氶┈閫婃柊瑙勫奖鍝嶈法澧冨崠瀹?",
        (Platform.AMAZON,),
    )
    assert not mentions_target_platform(
        "鏈湴浣撹偛璧涗簨涓捐",
        (Platform.AMAZON, Platform.TEMU),
    )


@pytest.mark.parametrize(
    "marker",
    [
        "姝ｅ湪杩涜瀹夊叏妫€鏌ワ紝璇风◢鍊?",
        "璇疯緭鍏ラ獙璇佺爜鍚庣户缁?",
        "璇风櫥褰曞悗缁х画闃呰",
        "浼氬憳涓撲韩鍐呭",
        "浠樿垂闃呰鍚庢煡鐪嬪叏鏂?",
    ],
)
def test_public_article_rejects_chinese_access_walls(marker: str) -> None:
    with pytest.raises(ArticleGateError, match="article_access_wall"):
        validate_public_article(
            body=article_html(f"浜氶┈閫婂崠瀹舵敞鎰忥細{marker}"),
            content_type="text/html",
            platforms=(Platform.AMAZON,),
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
