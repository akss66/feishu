# GDELT Controlled Original Fetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable all ten GDELT platform discovery sources and safely upgrade eligible public original articles from metadata leads to LLM-analyzable full text.

**Architecture:** Keep GDELT as a metadata-only discovery adapter, then apply a bounded, opt-in second-stage fetch for publisher profiles explicitly marked `allowed_public`. A dedicated article gate rejects access walls, non-HTML responses, incomplete pages, and platform-irrelevant content; every failure degrades to a metadata lead instead of dropping the article or failing the source. Full media bodies have bounded retention while summaries, evidence excerpts, attribution, hashes, and original links remain.

**Tech Stack:** Python 3.13, asyncio, httpx, Pydantic Settings, lxml/trafilatura, SQLAlchemy async, SQLite, pytest, Ruff.

## Global Constraints

- Scope is limited to Amazon, TEMU, SHEIN, AliExpress, Shopee, eBay, Coupang, Ozon, Joybuy, and TikTok Shop.
- GDELT discovery records remain `metadata_only`; only an eligible original article may produce `full_text`.
- Never bypass login walls, paywalls, CAPTCHA, JavaScript challenges, robots restrictions, 401, 403, 407, or 429.
- Accept only public HTTPS destinations validated by the existing SSRF-resistant client, with at most 3 redirects and a 10 MiB response limit.
- Original fetching is disabled by default and is enabled only after controlled smoke evidence is recorded.
- Unknown, authorization-required, licensed-only, metadata-only, and denied publishers never receive an original-article request.
- Media full text is retained for at most 7 days; derived analysis, short evidence, hashes, attribution, and original URLs remain.
- Failure to fetch or validate one article must not prevent other articles or the 09:00 report.
- Use TDD for every behavioral change and commit each task independently.

---

### Task 1: Add bounded original-fetch settings

**Files:**
- Modify: `src/commerce_agent/config.py`
- Modify: `src/commerce_agent/runtime.py`
- Modify: `.env.example`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_runtime.py`

**Interfaces:**
- Produces: `Settings.gdelt_original_fetch_enabled: bool`
- Produces: `Settings.gdelt_original_fetch_max_per_source: int`
- Produces: `Settings.gdelt_media_body_retention_days: Literal[7]`
- Consumes: `ApiCollector(..., fetch_gdelt_originals: bool, gdelt_original_fetch_limit: int)`

- [ ] **Step 1: Write failing configuration and runtime tests**

```python
def test_gdelt_original_fetch_defaults_are_conservative(valid_env):
    settings = Settings(**valid_env)
    assert settings.gdelt_original_fetch_enabled is False
    assert settings.gdelt_original_fetch_max_per_source == 5
    assert settings.gdelt_media_body_retention_days == 7

def test_runtime_passes_gdelt_fetch_controls_to_api_collector(monkeypatch):
    # Capture ApiCollector keyword arguments in the existing runtime fixture.
    assert captured["fetch_gdelt_originals"] is False
    assert captured["gdelt_original_fetch_limit"] == 5
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py tests/unit/test_runtime.py -q`
Expected: FAIL because the three settings and collector arguments do not exist.

- [ ] **Step 3: Add settings and runtime wiring**

```python
gdelt_original_fetch_enabled: bool = False
gdelt_original_fetch_max_per_source: int = Field(default=5, ge=1, le=25)
gdelt_media_body_retention_days: Literal[7] = 7
```

Pass the first two values into `ApiCollector` and the retention value into `IngestionService`. Add matching conservative entries to `.env.example`.

- [ ] **Step 4: Run focused tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py tests/unit/test_runtime.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add -- .env.example src/commerce_agent/config.py src/commerce_agent/runtime.py tests/unit/test_config.py tests/unit/test_runtime.py
git commit -m "feat: configure bounded GDELT article fetching"
```

### Task 2: Reject unusable original article responses

**Files:**
- Create: `src/commerce_agent/ingestion/article_gate.py`
- Create: `tests/unit/test_article_gate.py`

**Interfaces:**
- Produces: `ArticleGateError(code: str)`
- Produces: `validate_public_article(*, body: bytes, content_type: str | None, platforms: tuple[Platform, ...]) -> None`
- Consumes: `Platform`

- [ ] **Step 1: Write failing article-gate tests**

```python
def test_public_article_accepts_complete_platform_relevant_html():
    validate_public_article(
        body=b"<html><article><h1>Amazon seller fee update</h1>" + b"<p>Policy details.</p>" * 80 + b"</article></html>",
        content_type="text/html; charset=utf-8",
        platforms=(Platform.AMAZON,),
    )

@pytest.mark.parametrize(
    ("content_type", "body", "code"),
    [
        ("application/pdf", b"%PDF", "article_media_type_rejected"),
        ("text/html", b"<html>Sign in to continue</html>", "article_access_wall"),
        ("text/html", b"<html>Verify you are human CAPTCHA</html>", "article_access_wall"),
        ("text/html", b"<html><p>short</p></html>", "article_body_incomplete"),
        ("text/html", b"<html><p>Unrelated story</p>" * 100 + b"</html>", "article_platform_irrelevant"),
    ],
)
def test_public_article_rejects_unusable_pages(content_type, body, code):
    with pytest.raises(ArticleGateError, match=code):
        validate_public_article(
            body=body,
            content_type=content_type,
            platforms=(Platform.AMAZON,),
        )
```

- [ ] **Step 2: Run the new test and verify it fails**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_article_gate.py -q`
Expected: FAIL because `article_gate` does not exist.

- [ ] **Step 3: Implement the deterministic gate**

Implement:

- HTML/XHTML media-type allowlist.
- UTF-8 replacement decoding only for gate inspection.
- case-insensitive access-wall markers covering login, subscription, paywall, CAPTCHA, and challenge pages.
- minimum 300 visible characters after removing scripts, styles, navigation, headers, footers, and asides.
- platform aliases including exact marketplace names and localized `TikTok Shop`.
- stable error codes without embedding body text or URLs.

- [ ] **Step 4: Run article-gate tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_article_gate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add -- src/commerce_agent/ingestion/article_gate.py tests/unit/test_article_gate.py
git commit -m "feat: gate GDELT original article bodies"
```

### Task 3: Make GDELT second-stage fetching bounded and degradable

**Files:**
- Modify: `src/commerce_agent/ingestion/collectors/api.py`
- Modify: `src/commerce_agent/ingestion/service.py`
- Modify: `tests/unit/test_collectors.py`
- Modify: `tests/unit/test_ingestion_service.py`

**Interfaces:**
- Consumes: `validate_public_article(...)`
- Produces: `ApiCollector(http_port, *, publisher_lookup=..., fetch_gdelt_originals=False, gdelt_original_fetch_limit=5)`
- Produces: metadata fallback `CollectedItem` when an original fetch or gate fails

- [ ] **Step 1: Add failing collector tests**

Add tests proving:

```python
collector = ApiCollector(
    http,
    publisher_lookup=lambda _: allowed_profile,
    fetch_gdelt_originals=True,
    gdelt_original_fetch_limit=1,
)
```

- the first eligible original is fetched and becomes `full_text`;
- later candidates remain `metadata_only` after the per-source fetch budget is exhausted;
- `FetchError`, `UrlSafetyError`, non-HTML, access-wall, incomplete, and irrelevant responses return the original metadata item rather than disappearing;
- the source run is `SUCCESS` or `PARTIAL`, not wholly failed, when at least one metadata lead survives;
- publisher profiles other than `allowed_public` cause no original request;
- the original request uses no ETag or Last-Modified values from the GDELT response.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_collectors.py tests/unit/test_ingestion_service.py -q`
Expected: FAIL because the fetch switch, budget, gate, and fallback behavior are absent.

- [ ] **Step 3: Implement controlled fetch and fallback**

Refactor the GDELT item loop so it first builds one immutable metadata item. Attempt an original fetch only when:

```python
(
    self._fetch_gdelt_originals
    and profile.article_access is ArticleAccess.ALLOWED_PUBLIC
    and original_fetches < self._gdelt_original_fetch_limit
)
```

Catch only controlled fetch, URL-safety, and article-gate failures. Yield the metadata item on failure. Never catch cancellation. Increase the original-fetch counter before the network request so failures still consume budget.

- [ ] **Step 4: Add stable error classifications**

Add `article_access_wall`, `article_body_incomplete`, `article_media_type_rejected`, and `article_platform_irrelevant` to the controlled ingestion error set without logging article bodies.

- [ ] **Step 5: Run focused tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_collectors.py tests/unit/test_ingestion_service.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add -- src/commerce_agent/ingestion/collectors/api.py src/commerce_agent/ingestion/service.py tests/unit/test_collectors.py tests/unit/test_ingestion_service.py
git commit -m "feat: fetch eligible GDELT originals safely"
```

### Task 4: Bound GDELT media-body retention

**Files:**
- Modify: `src/commerce_agent/ingestion/service.py`
- Modify: `src/commerce_agent/persistence/ingestion.py`
- Modify: `src/commerce_agent/persistence/models.py`
- Modify: `tests/unit/test_ingestion_service.py`
- Modify: `tests/integration/test_ingestion_repository.py`
- Modify: `tests/integration/test_ingestion_pipeline.py`

**Interfaces:**
- Produces: `IngestionRepository.redact_expired_media_bodies(*, before: datetime) -> int`
- Produces: redacted body marker `"[media body expired; use analysis evidence and original link]"`
- Consumes: `SourceAdapter.GDELT`, configured 7-day retention

- [ ] **Step 1: Write failing retention tests**

Test that:

- every GDELT run prunes raw snapshots older than 7 days using `source.adapter`, not a legacy singular source ID;
- an analyzed GDELT `full_text` version older than 7 days has its body replaced by the fixed marker;
- unanalysed jobs and non-GDELT/official documents are unchanged;
- title, content hashes, provenance, analysis, evidence, attribution, and canonical URL remain.

- [ ] **Step 2: Run retention tests and verify failure**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_ingestion_service.py tests/integration/test_ingestion_repository.py tests/integration/test_ingestion_pipeline.py -q`
Expected: FAIL because retention is still tied to a legacy ID and database bodies are never redacted.

- [ ] **Step 3: Implement repository redaction and service cleanup**

Use a single SQLAlchemy update constrained by:

- `Source.adapter == "gdelt"`;
- provenance `content_scope == "full_text"`;
- completed `DocumentAnalysis` exists;
- `DocumentVersion.fetched_at < before`;
- body is not already the fixed marker.

Call it during initialization and after a GDELT source run. Prune snapshots with the same cutoff.

- [ ] **Step 4: Run retention tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_ingestion_service.py tests/integration/test_ingestion_repository.py tests/integration/test_ingestion_pipeline.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add -- src/commerce_agent/ingestion/service.py src/commerce_agent/persistence/ingestion.py src/commerce_agent/persistence/models.py tests/unit/test_ingestion_service.py tests/integration/test_ingestion_repository.py tests/integration/test_ingestion_pipeline.py
git commit -m "feat: expire retained GDELT media bodies"
```

### Task 5: Enable all ten safe discovery sources

**Files:**
- Modify: `src/commerce_agent/sources/public_sources.yaml`
- Modify: `tests/unit/test_source_registry.py`
- Modify: `tests/smoke/test_public_sources.py`
- Modify: `docs/operations/ten-platform-source-acceptance.md`

**Interfaces:**
- Produces: exactly one enabled `metadata_only` GDELT source per `Platform`
- Preserves: strict effective full-text coverage calculation

- [ ] **Step 1: Change the registry test to require enabled GDELT discovery**

```python
gdelt = tuple(source for source in registry.sources if source.adapter is SourceAdapter.GDELT)
assert len(gdelt) == len(Platform)
assert all(source.enabled for source in gdelt)
assert all(source.compliance is ComplianceStatus.ALLOWED for source in gdelt)
assert all(source.content_scope is ContentScope.METADATA_ONLY for source in gdelt)
```

Also assert strict full-text effective-source counts do not increase merely because GDELT is enabled.

- [ ] **Step 2: Run source tests and verify failure**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_source_registry.py -q`
Expected: FAIL because all ten GDELT sources are disabled.

- [ ] **Step 3: Enable the ten YAML entries**

Set the GDELT anchor to `enabled: true`; inherited entries become enabled. Update compliance notes to state that discovery is enabled and original fetching remains controlled by the runtime switch.

- [ ] **Step 4: Make public smoke explicitly bounded**

Keep the default smoke skipped. When explicitly enabled, probe a maximum of one GDELT query in addition to the existing public sources, with zero retries and no original-article fetch. A 429 remains a recorded smoke failure and does not trigger bypass behavior.

- [ ] **Step 5: Run registry and smoke tests offline**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_source_registry.py tests/smoke/test_public_sources.py -q`
Expected: PASS with the live smoke skipped unless its environment flag is explicitly set.

- [ ] **Step 6: Commit**

```powershell
git add -- src/commerce_agent/sources/public_sources.yaml tests/unit/test_source_registry.py tests/smoke/test_public_sources.py docs/operations/ten-platform-source-acceptance.md
git commit -m "feat: enable ten-platform GDELT discovery"
```

### Task 6: Admit only reviewed public publishers

**Files:**
- Modify: `src/commerce_agent/media/catalog.py`
- Modify: `tests/unit/test_media_catalog.py`
- Modify: `docs/operations/ten-platform-source-acceptance.md`

**Interfaces:**
- Produces: immutable `PublisherProfile` entries with evidence-backed `ArticleAccess`
- Consumes: `ApiCollector.publisher_lookup`

- [ ] **Step 1: Add catalog invariants**

Test that:

- every `allowed_public` profile has non-empty exact `allowed_hosts`;
- no profile can combine `allowed_public` with a paywalled or authorization-required acceptance record;
- Reuters, AP, Bloomberg, Financial Times, CNBC, BBC, Digital Commerce 360, EcommerceBytes, Marketplace Pulse, and the Chinese industry candidates retain their existing non-public-fetch status unless the acceptance document contains explicit evidence.

- [ ] **Step 2: Run catalog tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_media_catalog.py -q`
Expected: PASS before any promotion and guard future unsafe promotions.

- [ ] **Step 3: Record reviewed public profiles**

Promote only profiles whose terms/robots/public access evidence already satisfies the acceptance rules. At minimum, preserve all current restricted profiles. Add an `allowed_public` profile only together with:

- exact host allowlist;
- terms URL and result;
- robots URL and result;
- anonymous public HTTP result;
- access-wall result;
- review date.

If no media publisher passes, commit no promotion and document “zero reviewed public media publishers”; GDELT still provides ten-platform metadata coverage.

- [ ] **Step 4: Run catalog and collector tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_media_catalog.py tests/unit/test_collectors.py -q`
Expected: PASS.

- [ ] **Step 5: Commit acceptance evidence**

```powershell
git add -- src/commerce_agent/media/catalog.py tests/unit/test_media_catalog.py docs/operations/ten-platform-source-acceptance.md
git commit -m "docs: record GDELT publisher access decisions"
```

### Task 7: Verify the complete offline pipeline and document operations

**Files:**
- Modify: `README.md`
- Modify: `docs/operations/source-ingestion-runbook.md`
- Modify: `docs/operations/ten-platform-source-acceptance.md`

**Interfaces:**
- Documents: enabling/disabling discovery independently from original fetching
- Documents: `analyzable` versus `lead_only` smoke outcomes

- [ ] **Step 1: Add operational documentation**

Document:

- all ten GDELT discovery sources are enabled;
- `GDELT_ORIGINAL_FETCH_ENABLED=false` remains the safe default;
- the exact environment switch and restart step after smoke approval;
- original fetch budget, access-wall behavior, 7-day retention, and rollback;
- strict coverage remains based on independent full-text publishers, not enabled metadata sources.

- [ ] **Step 2: Run the complete offline verification**

Run: `..\..\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS with the explicit live-network smoke skipped.

Run: `..\..\.venv\Scripts\python.exe -m ruff check .`
Expected: `All checks passed!`

Run: `git diff --check`
Expected: no errors.

- [ ] **Step 3: Commit documentation**

```powershell
git add -- README.md docs/operations/source-ingestion-runbook.md docs/operations/ten-platform-source-acceptance.md
git commit -m "docs: operate controlled GDELT article fetching"
```

### Task 8: Run controlled live smoke and set the production switch

**Files:**
- Modify: `docs/operations/ten-platform-source-acceptance.md`
- Modify after merge: `.env` (untracked local configuration only)

**Interfaces:**
- Produces: per-platform smoke status `analyzable` or `lead_only`
- Produces: final decision for `GDELT_ORIGINAL_FETCH_ENABLED`

- [ ] **Step 1: Run one bounded GDELT discovery probe**

Run with explicit network approval:

```powershell
$env:RUN_PUBLIC_SOURCE_SMOKE = "1"
..\..\.venv\Scripts\python.exe -m pytest tests/smoke/test_public_sources.py -q
Remove-Item Env:RUN_PUBLIC_SOURCE_SMOKE
```

Expected: one GDELT request is made with zero retries and no original fetch. Record HTTP status and timestamp.

- [ ] **Step 2: Probe each platform sequentially**

Run `commerce_agent.ingestion_cli probe <source-id>` for the ten `media-gdelt-*` IDs, one at a time. Keep original fetching disabled for this discovery acceptance pass. Stop the pass if GDELT returns 429; do not change identity, proxy, query cadence, or retry settings.

- [ ] **Step 3: Test eligible original publishers**

For each reviewed `allowed_public` publisher returned by discovery, make at most one controlled original request through the production HTTP client and record `analyzable` or `lead_only`. Do not request restricted or unknown publishers.

- [ ] **Step 4: Record evidence and decide the switch**

Set `GDELT_ORIGINAL_FETCH_ENABLED=true` in the local `.env` only if:

- GDELT discovery succeeds without 429;
- at least one reviewed `allowed_public` original passes the article gate;
- offline tests are green;
- no access-control bypass occurred.

Otherwise leave the switch false while keeping the ten discovery sources enabled.

- [ ] **Step 5: Run post-decision verification**

Run: `.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli sources coverage`

Run: `.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli health`

Confirm that enabled discovery counts improve while strict full-text coverage changes only for genuinely analyzable publishers.

- [ ] **Step 6: Commit the smoke evidence**

```powershell
git add -- docs/operations/ten-platform-source-acceptance.md
git commit -m "docs: record controlled GDELT smoke results"
```
