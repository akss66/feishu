# Spec: Firecrawl-first source ingestion

## Objective

Add Firecrawl to the existing cross-border intelligence ingestion pipeline so every
collectable source run calls Firecrawl once before the existing collector. Preserve the
current ten-platform source registry and use the Firecrawl result as a fallback document
when the existing collector cannot return a usable item.

## Tech Stack

- Python 3.11/3.12 application with asyncio
- Firecrawl Cloud v2 `POST /scrape`
- Existing `httpx` dependency for an async, minimal REST integration
- Existing Pydantic settings, collector protocols, SQLAlchemy persistence, and pytest suite

## Commands

- Install CLI and skills: `npx -y firecrawl-cli@latest init --all --yes --skip-auth`
- Unit tests: `.\.venv\Scripts\python.exe -m pytest tests\unit -q`
- Integration tests: `.\.venv\Scripts\python.exe -m pytest tests\integration -q`
- Lint: `.\.venv\Scripts\ruff.exe check src tests`
- Live collection: `.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli run --all`
- Intelligence health: `.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli health`

## Project Structure

- `src/commerce_agent/integrations/firecrawl.py`: fixed-endpoint Firecrawl v2 client
- `src/commerce_agent/ingestion/collectors/firecrawl.py`: collector decorator and fallback mapping
- `src/commerce_agent/config.py`: full runtime Firecrawl settings
- `src/commerce_agent/ingestion_cli.py`: administration CLI wiring
- `src/commerce_agent/runtime.py`: scheduled runtime wiring and resource cleanup
- `tests/unit/`: client, decorator, config, CLI, and runtime behavior tests

## Code Style

Use an async protocol at the application boundary and stable, secret-free error codes:

```python
try:
    document = await firecrawl.scrape(source.entry_url)
except FirecrawlError as error:
    yield CollectedFailure(error.code)
```

## Data Flow

1. The ingestion service completes its existing enabled/compliance checks.
2. The Firecrawl decorator calls `/v2/scrape` for the source entry URL with markdown,
   main-content extraction, and a bounded cache age. Calls are independently serialized
   and paced for the plan's per-minute limit; retryable 408/429/5xx responses honor
   `Retry-After` or use exponential backoff with jitter.
3. The existing RSS/API/HTML/sitemap collector still discovers its normal items.
4. If the existing collector yields at least one item, those items remain authoritative.
5. If it yields no item and Firecrawl returned useful markdown, the decorator yields the
   Firecrawl document as a fallback candidate.
6. Firecrawl failures produce a controlled error code but never expose credentials and never
   prevent the existing collector from running.
7. When the Firecrawl fallback successfully recovers a source, native-path failures are
   suppressed for source-health accounting so a usable source is not suspended.

## Testing Strategy

- Client tests cover request shape, response validation, 401/403, 429 recovery, pacing,
  concurrency, transport errors, secret redaction, and cleanup.
- Decorator tests prove Firecrawl is called once per source run, fallback precedence, and
  failure degradation.
- Runtime/CLI tests prove all configured collectors are wrapped only when a key exists and
  the Firecrawl client is closed.
- A live smoke test must pass through the actual application integration before claiming the
  feature works.

## Boundaries

- Always: load credentials from environment, use HTTPS hosted API by default, use stable
  error codes, keep original collectors, and close network resources.
- Ask first: self-hosting Firecrawl, changing the ten-platform registry, or enabling browser
  interaction.
- Never: commit or log credentials, send disabled/unapproved sources to Firecrawl, scrape
  every discovered article without a separate credit-budget decision, or silently report a
  failed Firecrawl call as successful coverage.

## Success Criteria

- Every enabled and compliance-approved source run attempts exactly one Firecrawl scrape.
- Existing collectors continue to run after Firecrawl failure.
- Firecrawl provides a persisted fallback document when the existing collector has no item.
- Authentication/rate-limit/transport failures are visible as secret-free error codes.
- Unit and integration tests pass, one real Firecrawl call succeeds through the application,
  and the August 17 report is sent to the bound Feishu group.

## Open Questions

None for the first version. Per-article Firecrawl enrichment is intentionally deferred to
avoid unbounded credit consumption.
