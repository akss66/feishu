from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from commerce_agent.ingestion.dns import CloudflareDohResolver
from commerce_agent.ingestion.security import UrlSafetyPolicy

DnsMode = Literal["system", "cloudflare_doh"]


class AsyncCloser(Protocol):
    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ResolverBundle:
    safety_policy: UrlSafetyPolicy
    resources: tuple[AsyncCloser, ...]


def build_resolver_bundle(mode: DnsMode) -> ResolverBundle:
    """Build the URL policy and every resolver resource it owns."""
    if mode == "system":
        return ResolverBundle(safety_policy=UrlSafetyPolicy(), resources=())
    if mode == "cloudflare_doh":
        resolver = CloudflareDohResolver()
        return ResolverBundle(
            safety_policy=UrlSafetyPolicy(resolver=resolver),
            resources=(resolver,),
        )
    raise ValueError("unsupported ingestion DNS mode")
