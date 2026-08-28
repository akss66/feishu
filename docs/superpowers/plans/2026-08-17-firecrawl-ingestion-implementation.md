# Implementation Plan: Firecrawl-first source ingestion

## Overview

Introduce one bounded Firecrawl `/scrape` attempt per approved source run, retaining existing
collectors for discovery and using Firecrawl only as a recovery document when they find
nothing. The implementation is additive and avoids the currently modified source registry
and collector files wherever possible.

## Architecture Decisions

- Use direct REST through the project's existing async `httpx` dependency instead of adding
  a synchronous SDK and thread-pool bridge.
- Decorate collector instances at composition time so ingestion service and source registry
  semantics stay unchanged.
- Make the integration active when `FIRECRAWL_API_KEY` is configured; absence of the optional
  key preserves existing behavior for tests and development environments.

## Task List

### Phase 1: Client foundation

- [x] Add failing tests for Firecrawl request/response and stable failures.
  - Acceptance: tests describe the v2 request and secret-free error behavior.
  - Verify: targeted test fails because the client does not exist.
  - Files: `tests/unit/test_firecrawl.py`
- [x] Implement the async Firecrawl REST client and settings.
  - Acceptance: targeted tests pass; client is closeable; no secret is logged/repr'd.
  - Verify: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_firecrawl.py tests\unit\test_config.py -q`
  - Files: `src/commerce_agent/integrations/firecrawl.py`, `src/commerce_agent/config.py`, `.env.example`

### Checkpoint: Client foundation

- [x] Targeted tests pass and the live Example Domain smoke request succeeds.

### Phase 2: Collector behavior

- [x] Add failing tests for exactly-once invocation, fallback precedence, and degradation.
  - Acceptance: behavior is independent of concrete RSS/API/HTML collector types.
  - Verify: targeted test fails before decorator implementation.
  - Files: `tests/unit/test_firecrawl_collector.py`
- [x] Implement the Firecrawl-first collector decorator.
  - Acceptance: tests pass and fallback produces a valid response artifact.
  - Verify: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_firecrawl_collector.py -q`
  - Files: `src/commerce_agent/ingestion/collectors/firecrawl.py`, `src/commerce_agent/ingestion/collectors/__init__.py`

### Checkpoint: Collector behavior

- [x] Original and decorated collector unit tests pass.

### Phase 3: Runtime wiring and delivery

- [x] Add failing composition tests for CLI/runtime wrapping and cleanup.
  - Acceptance: key-present and key-absent behavior are explicit.
  - Verify: targeted runtime/CLI tests fail before wiring.
  - Files: `tests/unit/test_runtime.py`, `tests/unit/test_ingestion_cli.py`
- [x] Wire Firecrawl into CLI and scheduled runtime.
  - Acceptance: all web collector instances are decorated when configured and client is owned
    by the existing resource lifecycle.
  - Verify: targeted tests, then full unit/integration suite and Ruff.
  - Files: `src/commerce_agent/runtime.py`, `src/commerce_agent/ingestion_cli.py`
- [x] Run live collection, generate August 17 intelligence, and send to Feishu.
  - Acceptance: real integration call is observed, send API returns a message ID, and outbox
    has no failed/pending entry.
  - Verify: ingestion CLI, intelligence health, and Feishu send result.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Firecrawl 429/5xx | Partial coverage | Serialize and pace requests, honor `Retry-After`, retry with backoff, then use original collector fallback |
| Credit consumption | Cost increase | Exactly one call per source run; no per-item scrape |
| Landing-page fallback noise | Lower relevance | Persist only when the original collector yields no item |
| Secret disclosure | Credential compromise | `SecretStr`, no request-header logging, Git secret scan |
| Existing dirty worktree | Accidental overwrite | Avoid modified registry/feed files and do not commit mixed work |

## Open Questions

None.
