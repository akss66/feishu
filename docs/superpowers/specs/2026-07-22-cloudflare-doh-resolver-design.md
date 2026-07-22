# Cloudflare DoH Resolver Design

Date: 2026-07-22
Status: Approved for implementation planning

## Context

The ingestion HTTP path already validates every initial and redirected URL against an
allowed host set, resolves the host, rejects the request if any returned address is not
public, and pins the outbound connection to the validated addresses. On the current
Windows workstation, Clash Verge/Mihomo TUN fake-IP DNS returns `28.0.0.0/8` and a
private `fdfe::/8` address. The private result correctly triggers
`destination_not_public`, so the approved eBay RSS source cannot be collected.

Adding one Mihomo fake-IP exception per source would couple the agent to a local proxy
configuration and would not scale as more marketplaces and media sources are approved.
The agent therefore needs an opt-in resolver that obtains real DNS answers without
weakening the existing SSRF boundary.

## Decision

Add a Cloudflare-only DNS-over-HTTPS resolver and select it through one setting:

```text
INGESTION_DNS_MODE=system|cloudflare_doh
```

`system` remains the default. `cloudflare_doh` is an explicit operator choice for the
local Windows environment. There is no automatic fallback from DoH to system DNS: if
DoH fails, ingestion fails closed with the existing safe `dns_resolution_failed` code.

The first version uses Cloudflare's JSON DoH API because it is the smallest provider-
specific implementation. Cloudflare notes that JSON DoH is not an IETF-standard schema,
so the provider-specific parsing will be isolated behind the existing `Resolver`
interface. A future wire-format or secondary-provider implementation can replace it
without changing URL policy or collectors.

References:

- https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/
- https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/
- https://developers.cloudflare.com/1.1.1.1/infrastructure/network-operators/

## Architecture

### Resolver component

Add a small async `CloudflareDohResolver` that satisfies the existing callable contract:

```python
Resolver = Callable[[str], Awaitable[Collection[str]]]
```

It owns a dedicated `httpx.AsyncClient`, queries A and AAAA records at the fixed
Cloudflare bootstrap endpoint `https://1.1.1.1/dns-query`, validates TLS normally, does
not follow redirects, does not inherit proxy environment variables, and exposes
`aclose()` for deterministic shutdown. Using the literal bootstrap address prevents the
DoH client from recursively depending on the fake system resolver. A live probe on the
target workstation has confirmed that this endpoint completes TLS and returns a valid
Cloudflare JSON response through the existing TUN route.

Each query has a fixed five-second timeout and a 64 KiB response-body limit. The resolver
accepts only HTTP 200, the expected JSON media type, DNS status `NOERROR`, a non-truncated
response, and syntactically valid A/AAAA answers. CNAME records may be present but are not
returned as connection addresses. Duplicate addresses are removed while preserving
response order. At least one A or AAAA address must remain.

The resolver does not decide whether an address is public. It returns syntactically valid
addresses to `UrlSafetyPolicy`, which remains the single authoritative public-address and
metadata-address enforcement boundary.

### Configuration and construction

`Settings.ingestion_dns_mode` is a strict literal with values `system` and
`cloudflare_doh`. `.env.example` documents the safe default `system`.

Both ingestion construction paths must use the same resolver factory:

- administrator CLI (`commerce_agent.ingestion_cli`);
- scheduled application runtime (`commerce_agent.runtime`).

In `system` mode, construction remains behaviorally identical to today. In
`cloudflare_doh` mode, the factory creates one `CloudflareDohResolver`, injects it into
`UrlSafetyPolicy`, and registers it for shutdown alongside the ingestion HTTP client.
Partial construction failures must close every resource already created.

No database schema, source registry, collector, Feishu, or intelligence-analysis changes
are included.

## Request Data Flow

1. A collector supplies a URL and its source-specific allowed host set.
2. `UrlSafetyPolicy` normalizes and validates scheme, port, credentials, and host.
3. The selected resolver obtains all A and AAAA connection addresses.
4. `UrlSafetyPolicy` rejects the entire result if it is empty, malformed, private,
   loopback, link-local, multicast, reserved, or metadata-related.
5. The existing ingestion HTTP transport pins the connection to the validated addresses
   while preserving the original HTTPS host and TLS verification.
6. Every redirect repeats the same host allowlist, resolution, public-address validation,
   and pinning sequence.

DoH changes only step 3. All later defenses remain unchanged.

## Failure and Logging Contract

The resolver must never include response bodies, full query URLs, or exception text in
operator output. Provider timeouts, TLS failures, non-200 responses, wrong media types,
oversized bodies, malformed JSON, DNS error statuses, truncated replies, and empty answers
surface to `UrlSafetyPolicy` as a generic resolver failure. Existing code maps that to
`dns_resolution_failed`.

Valid but non-public addresses continue to produce `destination_not_public`; they are not
silently discarded, because accepting only the public subset would weaken DNS-rebinding
protection.

There is no system-DNS fallback, cached stale response fallback, configurable DoH URL,
certificate bypass, or operator switch that disables public-address validation.

## Testing and Acceptance

Unit tests will cover:

- `system` is the default, `cloudflare_doh` is accepted, and any other mode is rejected;
- A-only, AAAA-only, mixed A/AAAA, CNAME chains, and duplicate answer handling;
- timeout, TLS/request failure, non-200, wrong content type, oversized response, malformed
  JSON, NXDOMAIN/SERVFAIL, truncated reply, empty answer, and invalid address handling;
- response or exception content never reaches safe error strings or logs;
- private or mixed public/private DoH answers are rejected by the unchanged
  `UrlSafetyPolicy`;
- CLI and scheduled runtime both select and close the configured resolver;
- construction failures close both resolver and HTTP resources.

The existing full unit and integration suite must remain green. A real-network acceptance
run will use a process-scoped environment override, not edit the user's `.env`:

```powershell
$env:INGESTION_DNS_MODE='cloudflare_doh'
python -m commerce_agent.ingestion_cli run --source ebay-newsroom-rss
```

Acceptance succeeds only if the command passes the existing URL-safety checks, records a
successful source run in local SQLite, and queues no Feishu delivery. Automatic ingestion
and all intelligence delivery flags remain off.

## Alternatives Considered

### Mihomo fake-IP filter per domain

Fastest one-off workaround, but every newly approved host needs local proxy configuration.
Rejected as the application's long-term dependency.

### Cloudflare primary with Google fallback

Improves resolver availability but adds another provider, schema/transport behavior, and
fallback state to the first release. Deferred until operational evidence shows it is
needed.

### Cloud deployment

Best eventual production topology, but the user does not currently plan to operate a
server. The resolver interface remains portable so this local-first decision does not
block a future migration.

## Out of Scope

- enabling the ingestion scheduler;
- enabling analysis, reports, alerts, or QA;
- automatically editing `.env` or Clash Verge configuration;
- configurable or arbitrary DoH endpoints;
- DNS response persistence or a shared cache;
- adding Google or another fallback resolver;
- changing source compliance states.
