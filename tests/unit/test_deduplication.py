from __future__ import annotations

import hashlib

import pytest

from commerce_agent.ingestion.dedupe import (
    canonicalize_url,
    content_group_hash,
    content_hash,
    fingerprint_document,
)


def test_canonicalizes_scheme_host_default_port_fragment_and_tracking_keys() -> None:
    assert canonicalize_url(
        "HTTPS://BÜCHER.Example:443/政策?utm_source=newsletter&b=2&gclid=abc&a=1#top"
    ) == "https://xn--bcher-kva.example/%E6%94%BF%E7%AD%96?a=1&b=2"
    assert canonicalize_url("http://Example.COM:80/path#fragment") == (
        "http://example.com/path"
    )


def test_keeps_non_default_port_and_business_parameters_in_stable_order() -> None:
    assert canonicalize_url(
        "https://Shop.Example:8443/item?variant=blue&knowledge_id=42&id=7&ref=partner"
    ) == (
        "https://shop.example:8443/item?id=7&knowledge_id=42&ref=partner&variant=blue"
    )


def test_keeps_duplicate_business_parameters_without_merging_distinct_urls() -> None:
    first = canonicalize_url("https://example.com/search?sku=2&sku=1&region=us")
    second = canonicalize_url("https://example.com/search?sku=2&sku=1&region=eu")

    assert first == "https://example.com/search?region=us&sku=1&sku=2"
    assert second == "https://example.com/search?region=eu&sku=1&sku=2"
    assert first != second


def test_unicode_paths_have_one_stable_nfc_percent_encoded_form() -> None:
    literal = canonicalize_url("https://example.com/cafe\u0301/商品")
    encoded = canonicalize_url(
        "https://example.com/caf%C3%A9/%E5%95%86%E5%93%81"
    )

    assert literal == "https://example.com/caf%C3%A9/%E5%95%86%E5%93%81"
    assert encoded == literal
    encoded_slash = canonicalize_url("https://example.com/a%2Fb")
    literal_slash = canonicalize_url("https://example.com/a/b")
    assert encoded_slash == "https://example.com/a%2Fb"
    assert encoded_slash != literal_slash


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "https:///missing-host",
        "https://user:password@example.com/private",
        "https://example.com:99999/path",
    ],
)
def test_rejects_urls_without_a_safe_canonical_http_identity(url: str) -> None:
    with pytest.raises(ValueError):
        canonicalize_url(url)


def test_hashes_normalized_text_with_sha256() -> None:
    composed = "Caf\u00e9 policy\n\nSecond line"
    decomposed = "  Cafe\u0301\u00a0 policy\r\n\r\n Second\tline "

    expected = hashlib.sha256(composed.encode("utf-8")).hexdigest()
    assert content_hash(composed) == expected
    assert content_hash(decomposed) == expected
    assert content_group_hash(decomposed) == expected


def test_changed_normalized_body_has_a_different_hash() -> None:
    assert content_hash("Seller fee is 5%") != content_hash("Seller fee is 6%")


def test_equal_bodies_at_different_urls_share_content_group_hash() -> None:
    original = fingerprint_document(
        "https://example.com/policy?utm_source=email", " Shared policy body "
    )
    copy = fingerprint_document(
        "https://mirror.example.net/copy?id=42", "Shared\u00a0policy body"
    )

    assert original.canonical_url == "https://example.com/policy"
    assert copy.canonical_url == "https://mirror.example.net/copy?id=42"
    assert original.content_group_hash == copy.content_group_hash
    assert original.content_hash == copy.content_hash
