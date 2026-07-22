from __future__ import annotations

import pytest

from commerce_agent.ingestion import bootstrap


class PolicySpy:
    def __init__(self, resolver: object | None = None) -> None:
        self.resolver = resolver


def test_system_mode_preserves_default_resolver_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bootstrap, "UrlSafetyPolicy", PolicySpy)

    bundle = bootstrap.build_resolver_bundle("system")

    assert isinstance(bundle.safety_policy, PolicySpy)
    assert bundle.safety_policy.resolver is None
    assert bundle.resources == ()


def test_cloudflare_mode_injects_and_registers_one_shared_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = object()
    monkeypatch.setattr(bootstrap, "CloudflareDohResolver", lambda: resolver)
    monkeypatch.setattr(bootstrap, "UrlSafetyPolicy", PolicySpy)

    bundle = bootstrap.build_resolver_bundle("cloudflare_doh")

    assert isinstance(bundle.safety_policy, PolicySpy)
    assert bundle.safety_policy.resolver is resolver
    assert bundle.resources == (resolver,)


def test_unknown_dns_mode_is_rejected_defensively() -> None:
    with pytest.raises(ValueError, match="unsupported ingestion DNS mode"):
        bootstrap.build_resolver_bundle("other")  # type: ignore[arg-type]
