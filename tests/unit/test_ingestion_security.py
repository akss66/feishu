from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from dataclasses import replace
from datetime import date

import pytest

from commerce_agent.ingestion.compliance import CompliancePolicy, CompliancePolicyError
from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    Platform,
    SourceDefinition,
    TrustTier,
)
from commerce_agent.ingestion.security import UrlSafetyError, UrlSafetyPolicy

Resolver = Callable[[str], Awaitable[Collection[str]]]


def _source(**overrides: object) -> SourceDefinition:
    source = SourceDefinition(
        source_id="approved-source",
        name="Approved source",
        entry_url="https://news.example.com/feed",
        platforms=(Platform.AMAZON,),
        trust_tier=TrustTier.OFFICIAL,
        collector=CollectorKind.RSS,
        compliance=ComplianceStatus.ALLOWED,
        enabled=True,
        regions=("global",),
        language_hint="en",
        interval_minutes=120,
        terms_url="https://news.example.com/terms",
        robots_url="https://news.example.com/robots.txt",
        reviewed_at=date(2026, 7, 20),
        compliance_notes="Public feed approved after terms review.",
    )
    return replace(source, **overrides)


def _resolver(addresses_by_host: dict[str, Collection[str]]) -> Resolver:
    async def resolve(host: str) -> Collection[str]:
        return addresses_by_host[host]

    return resolve


def test_compliance_allows_only_enabled_allowed_sources() -> None:
    CompliancePolicy().require_collectable(_source())


@pytest.mark.parametrize(
    "status",
    [
        ComplianceStatus.PENDING_REVIEW,
        ComplianceStatus.DENIED,
        ComplianceStatus.AUTHORIZATION_REQUIRED,
    ],
)
def test_non_allowed_compliance_stops_before_collector_invocation(
    status: ComplianceStatus,
) -> None:
    collector_calls = 0

    def invoke_collector(source: SourceDefinition) -> None:
        nonlocal collector_calls
        CompliancePolicy().require_collectable(source)
        collector_calls += 1

    with pytest.raises(CompliancePolicyError) as raised:
        invoke_collector(_source(compliance=status))

    assert raised.value.code == "compliance_not_allowed"
    assert collector_calls == 0


def test_disabled_source_stops_before_collector_invocation() -> None:
    collector_calls = 0

    def invoke_collector(source: SourceDefinition) -> None:
        nonlocal collector_calls
        CompliancePolicy().require_collectable(source)
        collector_calls += 1

    with pytest.raises(CompliancePolicyError) as raised:
        invoke_collector(_source(enabled=False))

    assert raised.value.code == "source_disabled"
    assert collector_calls == 0


@pytest.mark.asyncio
async def test_allows_allowlisted_public_https_url_and_resolves_every_address() -> None:
    resolved_hosts: list[str] = []

    async def resolve(host: str) -> Collection[str]:
        resolved_hosts.append(host)
        return ["93.184.216.34", "2606:4700:4700::1111"]

    safe = await UrlSafetyPolicy(resolver=resolve).validate(
        "HTTPS://News.Example.com:443/articles/1?cursor=next#section",
        {"news.example.com"},
    )

    assert safe.url == "https://news.example.com/articles/1?cursor=next"
    assert safe.host == "news.example.com"
    assert safe.resolved_addresses == ("93.184.216.34", "2606:4700:4700::1111")
    assert resolved_hosts == ["news.example.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "allowed_hosts", "expected_code"),
    [
        ("file:///etc/passwd", {"example.com"}, "scheme_not_allowed"),
        ("data:text/plain,private", {"example.com"}, "scheme_not_allowed"),
        ("http://example.com/news", {"example.com"}, "scheme_not_allowed"),
        ("https://user:password@example.com/news", {"example.com"}, "userinfo_not_allowed"),
        ("https://example.com:8443/news", {"example.com"}, "port_not_allowed"),
        ("https://notexample.com/news", {"example.com"}, "host_not_allowed"),
        ("https://example.com.attacker.test/news", {"example.com"}, "host_not_allowed"),
        ("https://localhost/news", {"localhost"}, "destination_not_public"),
        ("https://service.localhost/news", {"service.localhost"}, "destination_not_public"),
        ("https://127.0.0.1/news", {"127.0.0.1"}, "destination_not_public"),
        ("https://10.0.0.1/news", {"10.0.0.1"}, "destination_not_public"),
        ("https://172.16.0.1/news", {"172.16.0.1"}, "destination_not_public"),
        ("https://192.168.0.1/news", {"192.168.0.1"}, "destination_not_public"),
        ("https://169.254.1.1/news", {"169.254.1.1"}, "destination_not_public"),
        ("https://192.0.2.1/news", {"192.0.2.1"}, "destination_not_public"),
        ("https://0.0.0.0/news", {"0.0.0.0"}, "destination_not_public"),
        ("https://224.0.0.1/news", {"224.0.0.1"}, "destination_not_public"),
        ("https://[::1]/news", {"::1"}, "destination_not_public"),
        ("https://[fc00::1]/news", {"fc00::1"}, "destination_not_public"),
        ("https://[fe80::1]/news", {"fe80::1"}, "destination_not_public"),
        ("https://[::]/news", {"::"}, "destination_not_public"),
        ("https://[ff02::1]/news", {"ff02::1"}, "destination_not_public"),
        ("https://[::ffff:127.0.0.1]/news", {"::ffff:127.0.0.1"}, "destination_not_public"),
        (
            "https://metadata.google.internal/computeMetadata/v1/",
            {"metadata.google.internal"},
            "destination_not_public",
        ),
        (
            "https://instance-data.ec2.internal/latest/meta-data/",
            {"instance-data.ec2.internal"},
            "destination_not_public",
        ),
        (
            "https://169.254.169.254/latest/meta-data/",
            {"169.254.169.254"},
            "destination_not_public",
        ),
        (
            "https://100.100.100.200/latest/meta-data/",
            {"100.100.100.200"},
            "destination_not_public",
        ),
    ],
)
async def test_rejects_unsafe_or_unregistered_url_forms(
    url: str,
    allowed_hosts: set[str],
    expected_code: str,
) -> None:
    async def public_resolver(host: str) -> Collection[str]:
        return ["93.184.216.34"]

    with pytest.raises(UrlSafetyError) as raised:
        await UrlSafetyPolicy(resolver=public_resolver).validate(url, allowed_hosts)

    assert raised.value.code == expected_code
    assert url not in str(raised.value)


@pytest.mark.asyncio
async def test_rejects_host_when_any_dns_result_is_not_public() -> None:
    policy = UrlSafetyPolicy(
        resolver=_resolver({"news.example.com": ["93.184.216.34", "::ffff:192.168.1.10"]})
    )

    with pytest.raises(UrlSafetyError) as raised:
        await policy.validate("https://news.example.com/feed", {"news.example.com"})

    assert raised.value.code == "destination_not_public"


@pytest.mark.asyncio
async def test_rejects_empty_or_failed_dns_resolution_with_stable_codes() -> None:
    async def failed_resolver(host: str) -> Collection[str]:
        raise OSError(f"DNS failed for {host}?token=resolver-secret")

    empty_policy = UrlSafetyPolicy(resolver=_resolver({"news.example.com": []}))
    with pytest.raises(UrlSafetyError) as empty:
        await empty_policy.validate("https://news.example.com/feed", {"news.example.com"})
    assert empty.value.code == "dns_resolution_failed"

    with pytest.raises(UrlSafetyError) as failed:
        await UrlSafetyPolicy(resolver=failed_resolver).validate(
            "https://news.example.com/feed?token=request-secret", {"news.example.com"}
        )
    assert failed.value.code == "dns_resolution_failed"
    assert "resolver-secret" not in str(failed.value)
    assert "request-secret" not in str(failed.value)


@pytest.mark.asyncio
async def test_redirect_target_is_revalidated_and_private_resolution_is_rejected() -> None:
    policy = UrlSafetyPolicy(
        resolver=_resolver(
            {
                "news.example.com": ["93.184.216.34"],
                "redirect.example.com": ["10.0.0.8"],
            }
        )
    )
    allowed_hosts = {"news.example.com", "redirect.example.com"}

    await policy.validate("https://news.example.com/start", allowed_hosts)
    with pytest.raises(UrlSafetyError) as redirect:
        await policy.validate("https://redirect.example.com/final", allowed_hosts)

    assert redirect.value.code == "destination_not_public"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://user:password@News.Example.com/path/to/item?token=query-secret#fragment",
            "https://news.example.com/path/to/item",
        ),
        ("https://news.example.com", "https://news.example.com/"),
    ],
)
def test_redacts_url_to_scheme_host_and_path_only(raw: str, expected: str) -> None:
    assert UrlSafetyPolicy().redact_for_log(raw) == expected


def test_redacts_urls_headers_cookies_and_tokens_from_exception_text() -> None:
    error = RuntimeError(
        "GET https://user:password@news.example.com/path?token=query-secret#part failed\n"
        "Authorization: Bearer auth-secret\n"
        "Cookie: session=cookie-secret\n"
        "Set-Cookie: refresh=cookie-two\n"
        "X-Api-Key: api-secret\n"
        "access_token=token-secret"
    )

    redacted = UrlSafetyPolicy().redact_for_log(error)

    assert "https://news.example.com/path" in redacted
    for secret in (
        "user",
        "password",
        "query-secret",
        "fragment",
        "auth-secret",
        "cookie-secret",
        "cookie-two",
        "api-secret",
        "token-secret",
    ):
        assert secret not in redacted
    assert redacted.count("[REDACTED]") >= 5
