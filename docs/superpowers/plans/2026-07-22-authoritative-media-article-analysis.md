# Authoritative Media Article Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover reports about the ten configured marketplaces, fetch original article text only when the publisher policy allows it, and give the LLM grounded full-text material for Chinese summaries, impact analysis, evidence, and actions.

**Architecture:** Keep GDELT as a metadata discovery source. Add a code-owned publisher catalog that normalizes domains, classifies publishers, and gates original-article access. The GDELT adapter persists metadata for approved catalog publishers, but only follows article URLs for `allowed_public`; licensed publishers use direct API connectors. Metadata-only documents remain auditable but are excluded from detailed LLM analysis.

**Tech Stack:** Python 3.11, dataclasses/StrEnum, existing HTTPX `HttpPort`, Trafilatura/lxml, SQLAlchemy/SQLite, pytest, Ruff, APScheduler.

## Global Constraints

- Platforms remain exactly Amazon, TEMU, SHEIN, AliExpress, Shopee, eBay, Coupang, Ozon, Joybuy, and TikTok Shop.
- GDELT remains one HTTPS request per run, at most 50 results, every 120 minutes.
- Original pages may be fetched only for `allowed_public`; `licensed_api` requires a separate approved connector.
- Never bypass login, paywall, CAPTCHA, robots, anti-bot controls, or publisher authorization requirements.
- Article snapshots retain body text and provenance for 30 days by default; Feishu never sends the complete article.
- A single media publisher remains capped at evidence confidence 70.
- All URLs pass existing SSRF, DNS, redirect, size, timeout, and rate-limit checks.
- Existing three-failure circuit breaker and manual-success recovery remain active.
- Never read, print, rewrite, or commit `.env` or secrets.

---

### Task 1: Code-owned publisher catalog and access policy

**Files:**
- Create: `src/commerce_agent/media/__init__.py`
- Create: `src/commerce_agent/media/catalog.py`
- Create: `tests/unit/test_media_catalog.py`

**Interfaces:**
- Produces: `MediaCategory`, `ArticleAccess`, `PublisherProfile`, `publisher_profile(hostname: str) -> PublisherProfile | None`, and `publisher_name(publisher_key: str) -> str`.
- Consumes: no application service; this is a pure immutable lookup boundary.

- [ ] **Step 1: Write failing catalog tests**

```python
from commerce_agent.media.catalog import (
    ArticleAccess,
    MediaCategory,
    publisher_profile,
)


def test_catalog_normalizes_www_without_suffix_confusion() -> None:
    profile = publisher_profile("www.reuters.com")
    assert profile is not None
    assert profile.publisher_key == "reuters.com"
    assert profile.display_name == "Reuters"
    assert profile.category is MediaCategory.GLOBAL_AUTHORITY
    assert profile.article_access is ArticleAccess.AUTHORIZATION_REQUIRED
    assert publisher_profile("reuters.com.example") is None


def test_catalog_includes_three_media_categories() -> None:
    assert publisher_profile("apnews.com").category is MediaCategory.GLOBAL_AUTHORITY
    assert publisher_profile("retaildive.com").category is MediaCategory.SPECIALIST
    assert publisher_profile("cifnews.com").category is MediaCategory.CHINESE_INDUSTRY
```

- [ ] **Step 2: Run RED**

Run: `C:\Users\AKSSINA\.python\python.exe -m pytest tests/unit/test_media_catalog.py -q`

Expected: FAIL because `commerce_agent.media.catalog` does not exist.

- [ ] **Step 3: Implement the catalog contract**

```python
class MediaCategory(StrEnum):
    GLOBAL_AUTHORITY = "global_authority"
    SPECIALIST = "specialist"
    CHINESE_INDUSTRY = "chinese_industry"


class ArticleAccess(StrEnum):
    ALLOWED_PUBLIC = "allowed_public"
    LICENSED_API = "licensed_api"
    AUTHORIZATION_REQUIRED = "authorization_required"
    METADATA_ONLY = "metadata_only"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class PublisherProfile:
    publisher_key: str
    display_name: str
    category: MediaCategory
    article_access: ArticleAccess
    allowed_hosts: tuple[str, ...]
```

Populate the exact catalog from the design. Reuters, AP, Bloomberg, FT, CNBC, BBC, Retail Dive, Digital Commerce 360, EcommerceBytes, and Modern Retail start as `authorization_required`; Marketplace Pulse remains `denied`; Chinese publishers remain `metadata_only` until their audit. Match only exact domains or subdomains.

- [ ] **Step 4: Run GREEN**

Run: `C:\Users\AKSSINA\.python\python.exe -m pytest tests/unit/test_media_catalog.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/commerce_agent/media tests/unit/test_media_catalog.py
git commit -m "feat: add governed media publisher catalog"
```

### Task 2: Filter GDELT results through the catalog

**Files:**
- Modify: `src/commerce_agent/ingestion/collectors/api.py`
- Modify: `tests/fixtures/ingestion/gdelt_articles.json`
- Modify: `tests/unit/test_collectors.py`
- Modify: `tests/unit/test_source_registry.py`

**Interfaces:**
- Consumes: `publisher_profile(hostname)`.
- Produces: GDELT `CollectedItem` instances only for catalog publishers, with normalized `publisher_key`.

- [ ] **Step 1: Add failing fixture cases**

Add a valid Reuters article, an unknown publisher, and a deceptive `reuters.com.example` article. Assert:

```python
items = await collected(ApiCollector(http), definition)
assert [item.publisher_key for item in items] == ["reuters.com"]
assert [item.url for item in items] == [
    "https://www.reuters.com/world/example-story/"
]
```

- [ ] **Step 2: Run RED**

Run: `C:\Users\AKSSINA\.python\python.exe -m pytest tests/unit/test_collectors.py::test_gdelt_adapter_keeps_safe_article_metadata_and_publisher_identity -q`

Expected: FAIL because unknown catalog publishers are not filtered.

- [ ] **Step 3: Add the catalog gate after domain/URL validation**

```python
profile = publisher_profile(publisher_key)
if profile is None or profile.article_access is ArticleAccess.DENIED:
    continue
publisher_key = profile.publisher_key
```

Keep `_gdelt_publisher_key` as the first security boundary.

- [ ] **Step 4: Verify focused tests**

Run: `C:\Users\AKSSINA\.python\python.exe -m pytest tests/unit/test_collectors.py tests/unit/test_source_registry.py -q`

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/commerce_agent/ingestion/collectors/api.py tests/fixtures/ingestion/gdelt_articles.json tests/unit/test_collectors.py tests/unit/test_source_registry.py
git commit -m "feat: restrict gdelt discovery to reviewed publishers"
```

### Task 3: Fetch allowed original articles and suppress metadata-only analysis

**Files:**
- Modify: `src/commerce_agent/ingestion/models.py`
- Modify: `src/commerce_agent/ingestion/collectors/api.py`
- Modify: `src/commerce_agent/ingestion/extract.py`
- Modify: `src/commerce_agent/ingestion/service.py`
- Modify: `src/commerce_agent/ingestion/snapshots.py`
- Modify: `src/commerce_agent/intelligence/repository.py`
- Modify: `tests/unit/test_collectors.py`
- Modify: `tests/unit/test_content_extraction.py`
- Modify: `tests/unit/test_ingestion_service.py`
- Modify: `tests/unit/test_snapshot_store.py`
- Modify: `tests/integration/test_intelligence_repository.py`

**Interfaces:**
- Produces: `CollectedItem.content_scope: ContentScope | None`.
- Produces: original HTML for `allowed_public`; sanitized metadata JSON for other non-denied profiles.
- Consumes: existing `FetchRequest`, `ContentExtractor`, snapshots, provenance persistence, and evidence identity.

- [ ] **Step 1: Write failing follow-up fetch tests**

Inject a catalog lookup into `ApiCollector` so tests can provide a fixture profile:

```python
collector = ApiCollector(
    http,
    publisher_lookup=lambda _: PublisherProfile(
        publisher_key="publisher.example",
        display_name="Fixture Publisher",
        category=MediaCategory.SPECIALIST,
        article_access=ArticleAccess.ALLOWED_PUBLIC,
        allowed_hosts=("publisher.example",),
    ),
)
items = await collected(collector, definition)
assert len(http.requests) == 2
assert items[0].body == b"<html><article>Original article body</article></html>"
assert items[0].content_scope is ContentScope.FULL_TEXT
```

Add parameterized cases proving `authorization_required`, `metadata_only`, `licensed_api`, and `denied` never request the article page.

- [ ] **Step 2: Run RED**

Run: `C:\Users\AKSSINA\.python\python.exe -m pytest tests/unit/test_collectors.py -k "gdelt and original" -q`

Expected: FAIL because no policy-aware follow-up fetch exists.

- [ ] **Step 3: Add item-level scope and guarded fetch**

```python
@dataclass(frozen=True, slots=True)
class CollectedItem:
    content_scope: ContentScope | None = None
```

For `allowed_public`, request the exact HTTPS article URL with `allowed_hosts=profile.allowed_hosts`, no conditional headers, and existing metrics. Yield the original response body/content type/artifact. For every other non-denied profile, yield sanitized metadata without a second request.

- [ ] **Step 4: Persist item-level provenance and exclude metadata-only rows**

In `_media_provenance`, prefer `item.content_scope` over `source.content_scope`. Derive media category later from `publisher_key`, so it cannot drift from the catalog. In the intelligence repository claim query, exclude persisted `content_scope == "metadata_only"`; such rows remain stored for audit but never reach `candidate_payload`.

- [ ] **Step 5: Add the 30-day raw snapshot retention boundary**

Add `SnapshotStore.prune_source_before(source_id: str, cutoff: datetime) -> int`. It may delete only gzip snapshot files resolved below the exact source directory and must reject invalid source IDs using the existing validation. Test that a 31-day-old media snapshot is removed, a 29-day-old snapshot remains, and a path outside the snapshot root is never touched. Invoke pruning for `media-gdelt-cross-border` before a manual or scheduled run, using a cutoff of `clock() - timedelta(days=30)`.

- [ ] **Step 6: Run focused tests**

Run: `C:\Users\AKSSINA\.python\python.exe -m pytest tests/unit/test_collectors.py tests/unit/test_content_extraction.py tests/unit/test_ingestion_service.py tests/unit/test_snapshot_store.py tests/integration/test_intelligence_repository.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add src/commerce_agent/ingestion/models.py src/commerce_agent/ingestion/collectors/api.py src/commerce_agent/ingestion/extract.py src/commerce_agent/ingestion/service.py src/commerce_agent/ingestion/snapshots.py src/commerce_agent/intelligence/repository.py tests/unit/test_collectors.py tests/unit/test_content_extraction.py tests/unit/test_ingestion_service.py tests/unit/test_snapshot_store.py tests/integration/test_intelligence_repository.py
git commit -m "feat: gate media article retrieval by publisher policy"
```

### Task 4: Expose media provenance to the LLM and Feishu

**Files:**
- Modify: `src/commerce_agent/intelligence/analyzer.py`
- Modify: `src/commerce_agent/intelligence/reports.py`
- Modify: `tests/unit/test_intelligence_analyzer.py`
- Modify: `tests/unit/test_intelligence_reports.py`

**Interfaces:**
- Consumes: `AnalysisCandidate.publisher_key`, `attribution`, `content_scope`, and catalog profile.
- Produces: `article.media` in LLM payload and `media_category`, `content_basis`, and publisher display name in report items.

- [ ] **Step 1: Write failing payload/report tests**

```python
payload = candidate_payload(media_candidate)
assert payload["article"]["media"] == {
    "publisher_key": "reuters.com",
    "publisher_name": "Reuters",
    "category": "global_authority",
    "content_basis": "full_text",
}

media = _analysis(1)
media = replace(
    media,
    candidate=replace(
        media.candidate,
        publisher_key="reuters.com",
        attribution="GDELT index; original publisher shown per item",
        content_scope="full_text",
    ),
)
draft = DailyReportComposer().compose(
    report_date=date(2026, 7, 21),
    analyses=(media,),
)
item = draft.payload["items"][0]
assert item["source_name"] == "Reuters"
assert item["media_category"] == "global_authority"
assert item["content_basis"] == "full_text"
```

- [ ] **Step 2: Run RED**

Run: `C:\Users\AKSSINA\.python\python.exe -m pytest tests/unit/test_intelligence_analyzer.py tests/unit/test_intelligence_reports.py -q`

Expected: FAIL because the provenance fields are absent.

- [ ] **Step 3: Derive labels from the code-owned catalog**

Use `publisher_profile(candidate.publisher_key)` at rendering time. Do not persist authority labels as a second source of truth. Keep anchored-evidence checks, so every rationale quote must occur in the saved original body.

- [ ] **Step 4: Verify evidence behavior**

Run: `C:\Users\AKSSINA\.python\python.exe -m pytest tests/unit/test_intelligence_analyzer.py tests/unit/test_intelligence_reports.py tests/unit/test_intelligence_evidence.py -q`

Expected: all tests pass and one publisher remains capped at 70.

- [ ] **Step 5: Commit**

```powershell
git add src/commerce_agent/intelligence/analyzer.py src/commerce_agent/intelligence/reports.py tests/unit/test_intelligence_analyzer.py tests/unit/test_intelligence_reports.py
git commit -m "feat: label media evidence in ai reports"
```

### Task 5: Audit, live smoke, activation, and runtime verification

**Files:**
- Create: `docs/operations/media-source-compliance-review-2026-07-22.md`
- Modify: `src/commerce_agent/media/catalog.py`
- Modify: `src/commerce_agent/sources/public_sources.yaml`
- Modify: `tests/unit/test_source_registry.py`
- Modify: `docs/operations/source-ingestion-runbook.md`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: reviewed production policies, a live-tested GDELT source, zero unreviewed original-page requests, and an operator recovery procedure.

- [ ] **Step 1: Record official evidence per publisher**

For every catalog publisher, record entry URL, terms, robots, access state, reason, review date, and allowed content scope. Confirmed restricted publishers remain conservative; Chinese industry publishers stay metadata-only until evidence supports a change.

- [ ] **Step 2: Lock audit decisions in tests**

Assert exact catalog decisions and that GDELT remains `metadata_only`, `item_limit == 50`, and `interval_minutes == 120`.

Run: `C:\Users\AKSSINA\.python\python.exe -m pytest tests/unit/test_media_catalog.py tests/unit/test_source_registry.py -q`

Expected: pass only when catalog, registry, and audit agree.

- [ ] **Step 3: Run manual GDELT smoke while scheduling is disabled**

```powershell
$env:INGESTION_DNS_MODE='cloudflare_doh'
C:\Users\AKSSINA\.python\python.exe -m commerce_agent.ingestion_cli run --source media-gdelt-cross-border
```

Expected: one GDELT request, no more than 50 records, unknown/denied publishers discarded, and no original request for a non-`allowed_public` publisher. If the one-day window has no reviewed publishers, use a seven-day window for manual test only.

- [ ] **Step 4: Enable GDELT only after successful smoke**

Set `media-gdelt-cross-border.enabled: true`, update review evidence, and change the registry expectation to true. Do not mark a publisher `allowed_public` without source-specific evidence and an original-article smoke.

- [ ] **Step 5: Run full verification**

```powershell
C:\Users\AKSSINA\.python\python.exe -m pytest -q
C:\Users\AKSSINA\.python\python.exe -m ruff check src tests
git diff --check
```

Expected: pytest exit 0, Ruff reports `All checks passed!`, and diff check is empty.

- [ ] **Step 6: Restart exactly one bot**

Verify executable and command line before stopping the current PID. Restart from `C:\Users\AKSSINA\Desktop\feishu` with existing environment and `INGESTION_DNS_MODE=cloudflare_doh`. Confirm one `python.exe -m commerce_agent` process and both scheduler start messages.

- [ ] **Step 7: Commit**

```powershell
git add docs/operations/media-source-compliance-review-2026-07-22.md docs/operations/source-ingestion-runbook.md src/commerce_agent/media/catalog.py src/commerce_agent/sources/public_sources.yaml tests/unit/test_source_registry.py
git commit -m "feat: activate governed media discovery"
```
