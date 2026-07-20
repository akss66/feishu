# Public Source Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Apply `superpowers:test-driven-development` to every behavior change and `source-driven-development` when fixing third-party APIs or public source definitions.

**Goal:** Add a compliant, configuration-driven ingestion subsystem that registers public official and authoritative media sources for all 10 target platforms, collects allowed content every two hours or via CLI, and stores traceable, deduplicated, versioned originals without regressing the running Feishu bot.

**Architecture:** Extend the current modular monolith with an `ingestion` package. A version-controlled YAML registry feeds a single `IngestionService`; collector adapters fetch candidates, extractor and deduplicator normalize them, repositories persist documents and immutable versions, and a scheduler plus CLI call the same service. All network and filesystem edges are injected so tests remain deterministic and offline.

**Tech Stack:** Python 3.11, asyncio, SQLAlchemy 2.x async, SQLite/aiosqlite, Pydantic Settings, HTTPX 0.28, feedparser 6, Trafilatura 2, Lingua 2, APScheduler 3.11, optional Playwright 1.61, PyYAML, pytest/pytest-asyncio, Ruff.

**Approved design:** `docs/superpowers/specs/2026-07-20-source-ingestion-design.md`

## Global constraints

- Never inspect, print, copy, or commit `.env` values.
- Never bypass login, CAPTCHA, paywall, robots rules, access controls, rate limits, or source terms.
- Only `allowed` and enabled sources may make content requests.
- Reject unsafe URL schemes, local/private/link-local/metadata destinations, including after redirects.
- Do not log cookies, authorization headers, response bodies, secrets, or complete query strings.
- Default automated tests make no real network requests.
- Do not add AI summarization, translation, daily report composition, alert scoring, or Feishu push behavior in this phase.
- Each task follows red-green-refactor and ends with the listed focused tests plus the full regression suite before commit.

---

## Task 1: Add ingestion dependencies and validated settings

**Files:**

- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Modify: `.env.example`
- Modify: `src/commerce_agent/config.py`
- Modify: `tests/unit/test_config.py`

### Steps

- [ ] Add failing tests for all ingestion defaults and validation:
  - interval `120` minutes;
  - global concurrency `4`;
  - domain rate `1.0` request/second;
  - timeout `20` seconds;
  - maximum response `10_485_760` bytes;
  - browser disabled;
  - snapshot directory `./data/snapshots`;
  - nonblank user agent;
  - positive numeric limits and interval;
  - optional `INGESTION_SCHEDULER_ENABLED`, defaulting to `false` for staged rollout.
- [ ] Run `python -m pytest tests/unit/test_config.py -v` and confirm failures refer to missing fields.
- [ ] Add the `ingestion` optional dependency group with `httpx>=0.28.1,<1`, `feedparser>=6.0.12,<7`, `trafilatura>=2.1,<3`, `lingua-language-detector>=2.1.1,<3`, `APScheduler>=3.11.3,<4`, and `PyYAML>=6.0.2,<7`.
- [ ] Add the `browser` optional group with `playwright>=1.61,<2`.
- [ ] Implement typed settings with Pydantic bounds and `Path` conversion.
- [ ] Add the environment keys to `.env.example` with safe defaults and no real credentials.
- [ ] Ignore `/data/`, `*.db-shm`, and `*.db-wal` without weakening the existing `.env` protection.
- [ ] Install with `python -m pip install -e ".[dev,ingestion]"`.
- [ ] Run focused tests and `python -m ruff check src/commerce_agent/config.py tests/unit/test_config.py`.
- [ ] Commit: `build: add ingestion configuration and dependencies`.

---

## Task 2: Define source contracts and validate the registry

**Files:**

- Create: `src/commerce_agent/ingestion/__init__.py`
- Create: `src/commerce_agent/ingestion/models.py`
- Create: `src/commerce_agent/ingestion/registry.py`
- Create: `src/commerce_agent/sources/public_sources.yaml`
- Create: `tests/unit/test_source_registry.py`
- Create: `tests/fixtures/ingestion/valid_sources.yaml`
- Create: `tests/fixtures/ingestion/invalid_sources.yaml`

### Interfaces

```python
class SourceRegistry:
    @classmethod
    def from_yaml(cls, path: Path) -> "SourceRegistry": ...

    def require(self, source_id: str) -> SourceDefinition: ...
    def enabled(self) -> tuple[SourceDefinition, ...]: ...
    def platform_coverage(self) -> dict[Platform, CoverageStatus]: ...
```

### Steps

- [ ] Write failing tests for enum validation, duplicate `source_id`, malformed URLs, missing compliance evidence, enabled-but-not-allowed rejection, collector-specific fields, stable platform names, and deterministic ordering.
- [ ] Add a coverage test requiring exactly the 10 approved platform identifiers and at least one official or pending-authorization source per platform.
- [ ] Add a seed-count test requiring at least 30 registry entries and both `official` and `media` trust tiers.
- [ ] Run `python -m pytest tests/unit/test_source_registry.py -v` and confirm import/behavior failures.
- [ ] Implement immutable dataclasses/enums for platform, collector kind, trust tier, compliance status, coverage status, source definition, fetch context, collected item, extracted document, trigger, and run summary.
- [ ] Implement strict YAML parsing with unknown-key rejection and human-readable errors containing the source ID.
- [ ] Verify every initial real URL and its terms/robots evidence against current official or publisher documentation before marking it `allowed`. Keep uncertain Shopee/Ozon regional pages as `pending_review`; keep unauthorized AliExpress systematic collection as `authorization_required`.
- [ ] Populate at least 30 entries across official sources and Marketplace Pulse, EcommerceBytes, Digital Commerce 360, and Reuters discovery sources. A source may be disabled while pending review but must remain visible in coverage.
- [ ] Run focused tests and Ruff.
- [ ] Commit: `feat: add validated public source registry`.

---

## Task 3: Add ingestion persistence and repository contracts

**Files:**

- Modify: `src/commerce_agent/persistence/models.py`
- Create: `src/commerce_agent/persistence/ingestion.py`
- Modify: `src/commerce_agent/persistence/database.py`
- Create: `tests/integration/test_ingestion_repository.py`

### Interfaces

```python
class IngestionRepository(Protocol):
    async def sync_sources(self, sources: Sequence[SourceDefinition]) -> None: ...
    async def start_run(self, source_id: str, trigger: Trigger) -> int: ...
    async def find_document(self, source_id: str, canonical_url: str) -> StoredDocument | None: ...
    async def persist_version(self, candidate: PersistableDocument) -> PersistOutcome: ...
    async def finish_run(self, run_id: int, summary: RunSummary) -> None: ...
```

### Steps

- [ ] Write integration tests using a temporary SQLite database for source sync, platform mapping, fetch-run lifecycle, document uniqueness, immutable version uniqueness, current-version update, content-group hashes, and health aggregation.
- [ ] Add a concurrency test showing duplicate content inserts resolve to one version rather than raising an unhandled integrity error.
- [ ] Run `python -m pytest tests/integration/test_ingestion_repository.py -v` and confirm failures.
- [ ] Add SQLAlchemy models for `sources`, `source_platforms`, `fetch_runs`, `documents`, `document_versions`, and `source_health` with the design’s unique constraints and indexes.
- [ ] Implement a repository that keeps network/extraction work outside transactions and performs short atomic writes.
- [ ] Keep the existing `GroupBinding` schema and repository behavior unchanged.
- [ ] Run the focused tests, then `python -m pytest tests/integration -v` and Ruff.
- [ ] Commit: `feat: persist source runs and document versions`.

---

## Task 4: Enforce compliance, URL safety, and log redaction

**Files:**

- Create: `src/commerce_agent/ingestion/compliance.py`
- Create: `src/commerce_agent/ingestion/security.py`
- Create: `tests/unit/test_ingestion_security.py`

### Interfaces

```python
class CompliancePolicy:
    def require_collectable(self, source: SourceDefinition) -> None: ...

class UrlSafetyPolicy:
    async def validate(self, url: str, allowed_hosts: Collection[str]) -> SafeUrl: ...
    def redact_for_log(self, url: str) -> str: ...
```

### Steps

- [ ] Write failing tests that allow an approved public HTTPS URL and reject `file:`, `data:`, user-info URLs, localhost, loopback, RFC1918, link-local, IPv6 local ranges, cloud metadata hosts, unexpected ports, unregistered hosts, and a redirect that resolves to a private address.
- [ ] Add tests that all non-`allowed` compliance states fail before collector invocation.
- [ ] Add log-redaction tests for query strings, fragments, credentials, authorization headers, cookies, and exception messages containing URLs.
- [ ] Run `python -m pytest tests/unit/test_ingestion_security.py -v` and confirm failures.
- [ ] Implement DNS resolution through an injectable resolver, revalidation on every redirect, allowed-host matching without unsafe suffix tricks, and stable classified exceptions.
- [ ] Implement safe URL rendering as scheme + host + path only.
- [ ] Run focused tests and Ruff.
- [ ] Commit: `feat: enforce ingestion safety boundaries`.

---

## Task 5: Build the bounded HTTP client, domain limiter, and snapshot store

**Files:**

- Create: `src/commerce_agent/ingestion/http.py`
- Create: `src/commerce_agent/ingestion/snapshots.py`
- Create: `tests/unit/test_ingestion_http.py`
- Create: `tests/unit/test_snapshot_store.py`

### Interfaces

```python
class IngestionHttpClient:
    async def get(self, request: FetchRequest) -> FetchResponse: ...

class SnapshotStore:
    async def save(self, source_id: str, response: FetchResponse) -> SnapshotRef: ...
```

### Steps

- [ ] Write failing HTTPX `MockTransport` tests for the 20-second configured timeout, global semaphore, per-domain spacing, safe redirect validation, conditional request headers, 304 handling, streamed size cap, retryable 429/5xx, `Retry-After`, nonretryable ordinary 4xx, and maximum three retries.
- [ ] Inject clock and sleeper functions so limiter and backoff tests do not sleep in real time.
- [ ] Write failing snapshot tests for gzip content, SHA-256 addressing, deterministic relative paths, idempotent writes, source ID path sanitization, and no request secrets in metadata.
- [ ] Implement the HTTP adapter using one shared `httpx.AsyncClient`, manual redirect handling, bounded streaming, and classified errors.
- [ ] Implement a lock-protected per-host limiter and global semaphore.
- [ ] Implement atomic snapshot writes using a temporary file in the target directory followed by replace; never overwrite different content at an existing hash path.
- [ ] Run focused tests and Ruff.
- [ ] Commit: `feat: add bounded fetching and snapshot storage`.

---

## Task 6: Implement generic collectors from offline fixtures

**Files:**

- Create: `src/commerce_agent/ingestion/collectors/__init__.py`
- Create: `src/commerce_agent/ingestion/collectors/base.py`
- Create: `src/commerce_agent/ingestion/collectors/feed.py`
- Create: `src/commerce_agent/ingestion/collectors/sitemap.py`
- Create: `src/commerce_agent/ingestion/collectors/html.py`
- Create: `src/commerce_agent/ingestion/collectors/api.py`
- Create: `src/commerce_agent/ingestion/collectors/browser.py`
- Create: `tests/unit/test_collectors.py`
- Create: `tests/fixtures/ingestion/feed.xml`
- Create: `tests/fixtures/ingestion/sitemap.xml`
- Create: `tests/fixtures/ingestion/list.html`
- Create: `tests/fixtures/ingestion/api.json`

### Steps

- [ ] Write failing fixture-based tests for RSS and Atom entries, nested sitemap indexes, namespace handling, configured HTML link selectors, public JSON path extraction, relative URL resolution, duplicate candidate links, invalid payloads, and item caps.
- [ ] Test that collectors only use the injected HTTP/browser port and never write persistence directly.
- [ ] Test that `BrowserCollector` returns a classified “renderer unavailable” result when browser support is disabled or Playwright is absent, without importing Playwright at module import time.
- [ ] Run `python -m pytest tests/unit/test_collectors.py -v` and confirm failures.
- [ ] Implement the common async collector protocol and four generic collectors.
- [ ] Implement the browser adapter behind a lazy optional import, with a fresh nonpersistent context, JavaScript execution timeout, no downloads, no service workers, and blocked non-HTTP navigation.
- [ ] Run focused tests and Ruff.
- [ ] Commit: `feat: add generic public content collectors`.

---

## Task 7: Extract, normalize, and identify language

**Files:**

- Create: `src/commerce_agent/ingestion/extract.py`
- Create: `src/commerce_agent/ingestion/dedupe.py`
- Create: `tests/unit/test_content_extraction.py`
- Create: `tests/unit/test_deduplication.py`
- Create: `tests/fixtures/ingestion/article_en.html`
- Create: `tests/fixtures/ingestion/article_zh.html`
- Create: `tests/fixtures/ingestion/article_ru.html`

### Steps

- [ ] Write failing tests for HTML boilerplate removal, source selector overrides, feed-provided text, title/author/published-time extraction, blank-content rejection, Unicode normalization, whitespace normalization, and preservation of original text.
- [ ] Write language tests for Chinese, English, and Russian plus short/ambiguous text returning `und` below the confidence threshold.
- [ ] Write URL canonicalization tests for lowercased scheme/host, fragment removal, default ports, tracking-key removal, sorted retained query parameters, Unicode paths, and business parameters that must remain.
- [ ] Write hashing tests for same normalized body, changed body, and equal bodies from different URLs sharing `content_group_hash`.
- [ ] Run the two focused test modules and confirm failures.
- [ ] Implement Trafilatura extraction with explicit selector overrides and safe timestamp parsing.
- [ ] Wrap Lingua behind a small injected `LanguageDetector` protocol so tests and future replacement remain simple.
- [ ] Implement deterministic canonicalization and SHA-256 utilities.
- [ ] Run focused tests and Ruff.
- [ ] Commit: `feat: extract and fingerprint source documents`.

---

## Task 8: Orchestrate idempotent ingestion and failure isolation

**Files:**

- Create: `src/commerce_agent/ingestion/service.py`
- Create: `tests/unit/test_ingestion_service.py`
- Create: `tests/integration/test_ingestion_pipeline.py`

### Steps

- [ ] Write failing unit tests for compliance-before-network, disabled-source skip, same-source lock, collector routing, snapshot-before-persist, per-item extraction failure accounting, and stable run summaries.
- [ ] Write a failing integration test that runs two fixture-backed sources through the full pipeline into temporary SQLite and snapshots.
- [ ] Prove in tests:
  - identical second run creates no version;
  - changed body creates exactly one new immutable version;
  - equal content from another URL is retained with a shared content-group hash;
  - one source returning 500 does not prevent another source from completing;
  - 304 updates health/run statistics but creates no content version.
- [ ] Run the focused tests and confirm failures.
- [ ] Implement `IngestionService.run_source()` and `run_all()` with per-source locks and bounded parallelism.
- [ ] Use structured, secret-safe logging fields and finish every started run in success, partial, skipped, or failed state.
- [ ] Run focused tests, the full suite, and Ruff.
- [ ] Commit: `feat: orchestrate idempotent source ingestion`.

---

## Task 9: Add the administrator CLI

**Files:**

- Create: `src/commerce_agent/ingestion_cli.py`
- Create: `tests/unit/test_ingestion_cli.py`

### Commands

```powershell
python -m commerce_agent.ingestion_cli sources list
python -m commerce_agent.ingestion_cli run --all
python -m commerce_agent.ingestion_cli run --source <source-id>
python -m commerce_agent.ingestion_cli health
```

### Steps

- [ ] Write failing tests for argument parsing, deterministic table output, unknown source handling, all/source mutual exclusion, exit codes 0/2/3, partial failure reporting, and query/secret redaction.
- [ ] Test CLI behavior through injected application factories; unit tests must not load `.env`, connect to Feishu/DeepSeek, or reach real sources.
- [ ] Run `python -m pytest tests/unit/test_ingestion_cli.py -v` and confirm failures.
- [ ] Implement an argparse-based async CLI that builds only database, registry, snapshot, HTTP, collector, extractor, and ingestion dependencies.
- [ ] Ensure `sources list` displays platform, trust tier, compliance, enabled state, collector, and coverage status even for pending/authorization-required sources.
- [ ] Run focused tests and Ruff.
- [ ] Commit: `feat: add source ingestion administration CLI`.

---

## Task 10: Schedule ingestion beside the Feishu connection

**Files:**

- Create: `src/commerce_agent/ingestion/scheduler.py`
- Modify: `src/commerce_agent/runtime.py`
- Modify: `tests/unit/test_feishu.py`
- Create: `tests/unit/test_ingestion_scheduler.py`
- Create: `tests/unit/test_runtime.py`

### Steps

- [ ] Write failing scheduler tests for the 120-minute interval, configured timezone, no immediate duplicate run, max one registered job, coroutine execution, and graceful shutdown.
- [ ] Write failing runtime lifecycle tests proving scheduler startup occurs before the blocking Feishu connect wait, ingestion exceptions do not stop the Feishu adapter, Feishu connection failure still shuts the scheduler down, and all resources close exactly once.
- [ ] Preserve the existing assertion that `FeishuChannel` uses `LogLevel.WARNING` so connection credentials cannot reappear in INFO logs.
- [ ] Run the focused tests and confirm failures.
- [ ] Implement an APScheduler `AsyncIOScheduler` wrapper with a stable job ID and `max_instances=1`, calling `IngestionService.run_all(Trigger.SCHEDULED)`.
- [ ] Refactor runtime composition into small factories where needed, create/sync schema and registry once, start the scheduler only when `INGESTION_SCHEDULER_ENABLED=true`, and keep a single Feishu WebSocket connection.
- [ ] Ensure shutdown order stops new schedules, closes the Feishu adapter/channel, closes HTTP/browser resources, closes DeepSeek, and disposes the database.
- [ ] Run focused tests, full tests, and Ruff.
- [ ] Commit: `feat: schedule ingestion with the Feishu runtime`.

---

## Task 11: Perform controlled source validation and finalize operator docs

**Files:**

- Modify: `README.md`
- Modify: `src/commerce_agent/sources/public_sources.yaml`
- Create: `tests/smoke/test_public_sources.py`
- Create: `docs/operations/source-ingestion-runbook.md`

### Steps

- [ ] Add opt-in smoke tests guarded by `RUN_PUBLIC_SOURCE_SMOKE=1`; default pytest must report them skipped without making a network request.
- [ ] Limit the smoke selection to a few `allowed` official sources, at most one list request and one detail request per source, and assert only safe reachability/content-type/candidate discovery properties.
- [ ] Run the default suite and verify smoke tests are skipped.
- [ ] With current source terms and robots evidence rechecked, run the smoke command manually. If a candidate blocks or redirects unexpectedly, mark it `pending_review` rather than weakening safety controls.
- [ ] Document installation, registry fields, manual commands, scheduler flag, browser opt-in, database/snapshot locations, health interpretation, safe source review, and rollback.
- [ ] Document that changing `.env` requires restarting the process, without displaying any current secret or binding code.
- [ ] Set `INGESTION_SCHEDULER_ENABLED=true` only after a successful manual run and an explicit operator decision; do not silently edit the user’s `.env` during this task.
- [ ] Commit: `docs: add source ingestion operations guide`.

---

## Task 12: Final verification and review

**Files:**

- Review all changed files from Tasks 1–11.

### Steps

- [ ] Run `python -m pytest -v` and record the exact pass/skip totals.
- [ ] Run `python -m ruff check .` and confirm zero findings.
- [ ] Run `git diff --check` and confirm no whitespace errors.
- [ ] Run `python -m commerce_agent.ingestion_cli sources list` and confirm all 10 platforms plus at least 30 sources are visible without secrets.
- [ ] Run a fixture-backed or approved-source manual ingestion twice and verify the second run creates no duplicate version.
- [ ] Run `python -m commerce_agent.ingestion_cli health` and confirm source/platform status is understandable.
- [ ] Start `python -m commerce_agent` with the scheduler disabled and manually verify the existing Feishu `帮助`, `状态`, and `AI测试` behavior still works.
- [ ] Scan tracked files and current logs for secret-shaped content, authorization headers, cookies, complete query strings, `access_key`, and SDK tickets; do not print matches containing secret values.
- [ ] Request an independent correctness/security review and resolve all high/medium findings.
- [ ] Commit any review fixes atomically.
- [ ] Do not merge or enable production scheduling without the user’s explicit approval.

## Completion criteria

The phase is complete only when all design acceptance criteria are demonstrated, the full automated suite and Ruff pass, all 10 platform coverage states are visible, at least 30 reviewed seed records exist, duplicate/version behavior is verified, the Feishu vertical slice remains functional, and no enabled source violates the compliance or access-control boundaries.
