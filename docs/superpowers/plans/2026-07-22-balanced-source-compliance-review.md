# Balanced Source Compliance Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit all 17 `pending_review` official sources under the approved balanced-open policy, enable only sources that pass evidence and live-smoke checks, and suspend repeatedly failing scheduled sources.

**Architecture:** The versioned YAML registry remains the authority for human-reviewed compliance decisions. An evidence report records first-party terms, robots, access boundaries, and live results. Runtime circuit breaking uses existing `SourceHealth.consecutive_failures` state: three consecutive non-successful runs mark a source `suspended`; scheduled collection skips suspended sources, while a manual run remains available to prove recovery and reset health.

**Tech Stack:** Python 3.11–3.12, PyYAML 6.x, httpx 0.28.x, SQLAlchemy 2.0–2.1 async, aiosqlite, pytest 8–9, Ruff 0.11+, APScheduler 3.11.x.

## Global Constraints

- Scope is exactly the 17 source IDs listed in `docs/superpowers/specs/2026-07-22-balanced-source-compliance-review-design.md`.
- A public page may be enabled when terms do not explicitly prohibit the limited automated use, robots does not disallow the target path, no authenticated user state is required, and the live smoke succeeds.
- Never use a user account, supplied Cookie, proxy rotation, CAPTCHA bypass, fingerprint spoofing, paywall bypass, or geographic-control bypass.
- An explicit prohibition produces `denied + disabled`; a prior-permission or login requirement produces `authorization_required + disabled`; unresolved conflicting evidence stays `pending_review + disabled`.
- Newly allowed sources use `interval_minutes: 120` and `collector_config.item_limit <= 20`.
- Only first-party entry pages, terms, robots, and first-party alternatives such as RSS/API are authoritative evidence.
- The existing allowed, denied, and authorization-required sources outside the 17-source scope must not change.
- `.env`, credentials, tokens, API keys, and authenticated cookies must never appear in commands, logs, diffs, reports, or commits.

---

### Task 1: Produce the first-party compliance audit

**Files:**
- Create: `docs/operations/source-compliance-review-2026-07-22.md`
- Read: `src/commerce_agent/sources/public_sources.yaml`
- Read: `docs/superpowers/specs/2026-07-22-balanced-source-compliance-review-design.md`

**Interfaces:**
- Consumes: the 17 source definitions and the approved decision matrix.
- Produces: one row per source with `decision`, `entry evidence`, `terms evidence`, `robots evidence`, `access result`, `reason`, and `live-smoke pending/result`; Tasks 3 and 4 consume these exact decisions.

- [ ] **Step 1: Record the baseline and exact scope**

Run:

```powershell
python -m commerce_agent.ingestion_cli sources list
git status --short
```

Expected: 17 target sources show `pending_review` and `enabled=no`; the worktree has no unrelated edits.

- [ ] **Step 2: Review Amazon first-party evidence**

Open and record the current content or safe failure for:

```text
https://sell.amazon.com/blog/
https://sell.amazon.com/blog/announcements
https://sellercentral.amazon.com/seller-forums
https://www.amazon.com/gp/help/customer/display.html?nodeId=508088
https://sell.amazon.com/robots.txt
https://sellercentral.amazon.com/robots.txt
```

Evaluate each target path independently. Do not apply the blog robots result to the Seller Central forums domain.

- [ ] **Step 3: Review Shopee first-party evidence by regional domain**

Open and record:

```text
https://seller.shopee.sg/edu/home
https://seller.shopee.com.my/edu/home
https://seller.shopee.ph/edu/home
https://shopee.sg/legaldoc/termsOfService/
https://shopee.com.my/legaldoc/termsOfService/
https://shopee.ph/legaldoc/termsOfService/
https://seller.shopee.sg/robots.txt
https://seller.shopee.com.my/robots.txt
https://seller.shopee.ph/robots.txt
```

Do not infer one country's result for another country unless all three official pages redirect to the same publisher-controlled policy and the report records that redirect.

- [ ] **Step 4: Review eBay first-party evidence by domain**

Open and record:

```text
https://www.ebayinc.com/stories/press-room/
https://www.ebayinc.com/terms-of-use/
https://www.ebayinc.com/robots.txt
https://www.ebay.com/sellercenter/news
https://www.ebay.com/help/policies/member-behaviour-policies/user-agreement?id=4259
https://www.ebay.com/robots.txt
```

Treat the already allowed newsroom RSS as an alternative for press-room coverage, not automatic permission to scrape full HTML articles.

- [ ] **Step 5: Review Coupang, Ozon, and Joybuy first-party evidence**

Open the three registered entry URLs for each platform plus:

```text
https://globalsellers.coupang.com/robots.txt
https://seller.ozon.ru/robots.txt
https://docs.ozon.ru/legal/en/partners/logistics/contract/
https://about.joybuy.com/robots.txt
https://about.joybuy.com/
```

Record every redirect hop, final publisher domain, authentication boundary, and whether the target content is present without user state.

- [ ] **Step 6: Write the audit report with no ambiguous decisions**

Use this exact table shape:

```markdown
| Source ID | Decision | Entry/access | Terms | Robots | Reason | Live smoke |
|---|---|---|---|---|---|---|
| amazon-seller-blog | allowed / denied / authorization_required / pending_review | first-party URL and result | first-party URL and relevant rule | first-party URL and target-path result | concise evidence-based explanation | pending |
```

For unavailable evidence, use a concrete safe explanation such as `unverified: official terms endpoint returned 503 during review` rather than inferring permission. Include a separate “Unchanged out-of-scope sources” section confirming no decision was made for the other 19 registered sources.

- [ ] **Step 7: Self-review and commit the evidence report**

Run:

```powershell
rg -n "待补充|稍后填写|未填写" docs/operations/source-compliance-review-2026-07-22.md
git diff --check
```

Expected: no placeholders and no whitespace errors.

Commit:

```powershell
git add docs/operations/source-compliance-review-2026-07-22.md
git commit -m "docs: record official source compliance audit"
```

---

### Task 2: Add a recoverable three-failure circuit breaker

**Files:**
- Modify: `src/commerce_agent/persistence/ingestion.py`
- Modify: `src/commerce_agent/ingestion/service.py`
- Modify: `tests/integration/test_ingestion_repository.py`
- Modify: `tests/unit/test_ingestion_service.py`
- Test: `tests/unit/test_ingestion_cli.py`

**Interfaces:**
- Produces: `SOURCE_FAILURE_THRESHOLD: Final[int] = 3` and `IngestionRepository.is_source_suspended(source_id: str) -> bool`.
- Behavior: a third consecutive partial/failed result stores `health_status="suspended"`; scheduled runs return `RunStatus.SKIPPED` with `error_code="source_circuit_open"`; manual runs bypass the circuit and a successful manual run resets failures and health.

- [ ] **Step 1: Write failing repository tests for opening and recovering the circuit**

Add tests that finish two failed runs and assert `is_source_suspended(...) is False`, finish a third and assert it is `True` with health status `suspended`, then finish one successful manual run and assert it is `False`, the failure count is zero, and status is `healthy`.

Use the existing `_repository`, `_source`, `RunSummary`, and `SourceHealth` fixtures rather than mocking SQLAlchemy.

- [ ] **Step 2: Run the repository tests and verify RED**

Run:

```powershell
python -m pytest tests/integration/test_ingestion_repository.py -q
```

Expected: FAIL because `is_source_suspended` does not exist and third failure currently stores `error`.

- [ ] **Step 3: Implement repository circuit state**

Add this public contract and implementation shape:

```python
from typing import Final

SOURCE_FAILURE_THRESHOLD: Final[int] = 3

class IngestionRepository(Protocol):
    async def is_source_suspended(self, source_id: str) -> bool: ...

class SqlAlchemyIngestionRepository:
    async def is_source_suspended(self, source_id: str) -> bool:
        async with self._session_factory() as session:
            status = await session.scalar(
                select(SourceHealth.health_status).where(
                    SourceHealth.source_id == source_id
                )
            )
        return status == "suspended"
```

In `finish_run`, increment `consecutive_failures` first, then store `suspended` when the count reaches `SOURCE_FAILURE_THRESHOLD`; keep existing `degraded`/`error` behavior below the threshold. A successful run must retain the existing reset to zero and `healthy`.

- [ ] **Step 4: Run repository tests and verify GREEN**

Run:

```powershell
python -m pytest tests/integration/test_ingestion_repository.py -q
```

Expected: PASS.

- [ ] **Step 5: Write failing service tests for scheduled skip and manual recovery**

Extend `FakeRepository` with `suspended_source_ids: set[str]` and:

```python
async def is_source_suspended(self, source_id: str) -> bool:
    return source_id in self.suspended_source_ids
```

Add one test proving a scheduled suspended source returns `source_circuit_open` without starting a run or calling the collector. Add a second test proving the same source still runs with `Trigger.MANUAL`.

- [ ] **Step 6: Run service tests and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_ingestion_service.py -q
```

Expected: FAIL because scheduled collection does not consult circuit state.

- [ ] **Step 7: Implement the scheduled circuit check**

Add `source_circuit_open` to `_KNOWN_ERROR_CODES`. After `_ensure_sources_synced()` and before claiming a lease, use:

```python
if (
    trigger is Trigger.SCHEDULED
    and await self._repository.is_source_suspended(source_id)
):
    return self._summary(
        source,
        trigger,
        started_at,
        RunStatus.SKIPPED,
        _RunCounts(error_code="source_circuit_open"),
        FetchMetrics(),
    )
```

Do not create a fetch run for a circuit-open skip; otherwise the skip would overwrite the suspended health state. Manual runs intentionally bypass this condition.

- [ ] **Step 8: Verify circuit breaker behavior and commit**

Run:

```powershell
python -m pytest tests/integration/test_ingestion_repository.py tests/unit/test_ingestion_service.py tests/unit/test_ingestion_cli.py -q
python -m ruff check src/commerce_agent/persistence/ingestion.py src/commerce_agent/ingestion/service.py tests/integration/test_ingestion_repository.py tests/unit/test_ingestion_service.py
git diff --check
```

Expected: all commands exit 0.

Commit:

```powershell
git add src/commerce_agent/persistence/ingestion.py src/commerce_agent/ingestion/service.py tests/integration/test_ingestion_repository.py tests/unit/test_ingestion_service.py
git commit -m "feat: suspend repeatedly failing ingestion sources"
```

---

### Task 3: Apply audited source decisions to the registry

**Files:**
- Modify: `src/commerce_agent/sources/public_sources.yaml`
- Modify: `tests/unit/test_source_registry.py`
- Read: `docs/operations/source-compliance-review-2026-07-22.md`

**Interfaces:**
- Consumes: the 17 explicit decisions in the Task 1 audit report.
- Produces: versioned source definitions with matching compliance status, enabled state, evidence URLs, review date, notes, and conservative item limits.

- [ ] **Step 1: Write the failing registry contract test**

Add an exact `BALANCED_REVIEW_SOURCE_IDS` set containing all 17 IDs. The new test must assert:

```python
reviewed = tuple(registry.require(source_id) for source_id in BALANCED_REVIEW_SOURCE_IDS)
assert all(source.reviewed_at == date(2026, 7, 22) for source in reviewed)
assert all(len(source.compliance_notes) >= 80 for source in reviewed)
assert all(source.enabled == (source.compliance is ComplianceStatus.ALLOWED) for source in reviewed)
assert all(
    source.collector_config.get("item_limit", 20) <= 20
    for source in reviewed
    if source.compliance is ComplianceStatus.ALLOWED
)
```

Import `date` from `datetime`. Also define and assert this exact out-of-scope baseline so unrelated decisions cannot drift:

```python
OUT_OF_SCOPE_STATUS = {
    "amazon-sp-api-changelog-rss": (ComplianceStatus.ALLOWED, True),
    "temu-seller-center": (ComplianceStatus.DENIED, False),
    "temu-about": (ComplianceStatus.DENIED, False),
    "temu-support-center": (ComplianceStatus.DENIED, False),
    "shein-group-newsroom": (ComplianceStatus.DENIED, False),
    "shein-group-press-releases": (ComplianceStatus.DENIED, False),
    "shein-group-company-updates": (ComplianceStatus.DENIED, False),
    "aliexpress-marketplace": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "aliexpress-seller-portal": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "aliexpress-terms-center": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "ebay-newsroom-rss": (ComplianceStatus.ALLOWED, True),
    "tiktok-shop-academy": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "tiktok-shop-policy-pulse": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "tiktok-shop-sg-seller-terms": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "media-digital-commerce-360-feed": (
        ComplianceStatus.AUTHORIZATION_REQUIRED,
        False,
    ),
    "media-ecommercebytes-feed": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
    "media-gdelt-cross-border": (ComplianceStatus.ALLOWED, False),
    "media-marketplace-pulse": (ComplianceStatus.DENIED, False),
    "media-reuters-retail": (ComplianceStatus.AUTHORIZATION_REQUIRED, False),
}

assert {
    source_id: (
        registry.require(source_id).compliance,
        registry.require(source_id).enabled,
    )
    for source_id in OUT_OF_SCOPE_STATUS
} == OUT_OF_SCOPE_STATUS
```

- [ ] **Step 2: Run the registry test and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_source_registry.py -q
```

Expected: FAIL because the 17 entries still have the 2026-07-20 review date and generic pending notes.

- [ ] **Step 3: Update each source from its audit row**

For every target source:

- set `reviewed_at: 2026-07-22`;
- copy only first-party `terms_url` and `robots_url` from verified evidence;
- write a source-specific `compliance_notes` summary that states access, terms, robots, and redirect findings;
- map the audit decision to `allowed`, `denied`, `authorization_required`, or `pending_review`;
- set `enabled: true` only for `allowed` entries;
- override inherited fields where regional evidence differs;
- set `collector_config.item_limit: 20` for every newly allowed HTML source.

Do not change any out-of-scope status.

- [ ] **Step 4: Verify registry parsing, safety constraints, and CLI presentation**

Run:

```powershell
python -m pytest tests/unit/test_source_registry.py tests/unit/test_ingestion_security.py tests/unit/test_ingestion_http.py -q
python -m commerce_agent.ingestion_cli sources list
python -m ruff check tests/unit/test_source_registry.py
git diff --check
```

Expected: tests and lint pass; the CLI shows only audited `allowed` entries as enabled.

- [ ] **Step 5: Commit the reviewed registry**

```powershell
git add src/commerce_agent/sources/public_sources.yaml tests/unit/test_source_registry.py
git commit -m "feat: apply balanced review to official sources"
```

---

### Task 4: Live-smoke every newly allowed source and finalize decisions

**Files:**
- Modify: `docs/operations/source-compliance-review-2026-07-22.md`
- Modify if a smoke fails: `src/commerce_agent/sources/public_sources.yaml`
- Modify if tested registry expectations change: `tests/unit/test_source_registry.py`
- Modify only when a verified page needs a selector correction: `tests/fixtures/ingestion/*.html`, `tests/unit/test_collectors.py`

**Interfaces:**
- Consumes: newly enabled source IDs from Task 3.
- Produces: a successful, persisted run for each remaining enabled source, or a recorded rollback to a disabled review state.

- [ ] **Step 1: Stop only the verified local bot process**

Resolve the exact `python -m commerce_agent` process by PID and command line. Stop only that PID so the scheduler cannot race with manual smoke tests. If the exact process cannot be verified, stop and report the blocker rather than terminating another Python process.

- [ ] **Step 2: Smoke sources sequentially**

Derive the newly enabled IDs from the approved 17-source set, then run one command at a time:

```powershell
$enabledSourceIds = @'
from pathlib import Path

import yaml

targets = {
    "amazon-seller-blog", "amazon-seller-announcements", "amazon-seller-forums",
    "shopee-sg-seller-education", "shopee-my-seller-education",
    "shopee-ph-seller-education", "ebay-press-room", "ebay-seller-updates",
    "coupang-rules-and-policies", "coupang-seller-university", "coupang-global-news",
    "ozon-seller-news", "ozon-seller-media", "ozon-global-docs",
    "joybuy-news", "joybuy-german-news", "joybuy-dutch-news",
}
registry = yaml.safe_load(
    Path("src/commerce_agent/sources/public_sources.yaml").read_text(encoding="utf-8")
)
for source in registry["sources"]:
    if (
        source["source_id"] in targets
        and source["compliance"] == "allowed"
        and source["enabled"] is True
    ):
        print(source["source_id"])
'@ | python -

$smokeResults = foreach ($sourceId in @($enabledSourceIds)) {
    python -m commerce_agent.ingestion_cli run --source $sourceId
    [pscustomobject]@{ SourceId = $sourceId; ExitCode = $LASTEXITCODE }
}
$smokeResults | Format-Table -AutoSize
```

Expected for acceptance: `status=success`, at least one discovered candidate or a valid not-modified/duplicate outcome, no authentication redirect, and no unsafe error code. Do not use `run --all` for initial acceptance.

- [ ] **Step 3: Roll back every failed source in the same work session**

If a source returns 401, 403, 429, CAPTCHA, login redirect, cross-publisher redirect, invalid selectors, or zero usable content:

- update its audit report row with the controlled failure;
- restore `enabled: false`;
- choose `pending_review`, `authorization_required`, or `denied` using the approved matrix;
- update its test expectation;
- do not retry with proxies, cookies, alternate identity, or higher request rate.

If the only failure is a selector mismatch on an otherwise accepted public page, first add a minimal sanitized HTML fixture and a failing collector test, verify RED, update only that source's selector, verify GREEN, then perform exactly one more live smoke.

- [ ] **Step 4: Verify persisted provenance and health**

Run:

```powershell
python -m commerce_agent.ingestion_cli health
python -m commerce_agent.intelligence_cli health
```

Expected: each accepted source has a successful last attempt; rolled-back sources are disabled and documented; no source is left enabled after a failed smoke.

- [ ] **Step 5: Finalize and commit live outcomes**

Run targeted tests for every changed selector plus:

```powershell
python -m pytest tests/unit/test_source_registry.py tests/unit/test_collectors.py tests/integration/test_ingestion_pipeline.py -q
python -m ruff check src tests
git diff --check
```

Expected: all commands exit 0.

Commit:

```powershell
git add docs/operations/source-compliance-review-2026-07-22.md src/commerce_agent/sources/public_sources.yaml tests/unit/test_source_registry.py tests/fixtures/ingestion tests/unit/test_collectors.py
git commit -m "test: verify audited sources with live collection"
```

---

### Task 5: Full regression, restart, and operational handoff

**Files:**
- Modify only if behavior changed: `docs/operations/source-ingestion-runbook.md`

**Interfaces:**
- Consumes: audited registry, live results, and circuit breaker.
- Produces: one healthy local bot process with both schedulers active and an evidence-backed final audit summary.

- [ ] **Step 1: Verify the complete repository**

Run:

```powershell
python -m pytest -q
python -m ruff check src tests
git diff --check
git status --short
```

Expected: pytest exits 0 with no failures, Ruff reports no errors, diff check is clean, and only intended documentation edits remain.

- [ ] **Step 2: Restart with the approved local runtime flags**

Start exactly one hidden `python -m commerce_agent` process with Cloudflare DoH, ingestion scheduler, analysis, daily report, alerts, and grounded QA enabled. Redirect stdout and stderr to timestamped files under `logs/`; do not print `.env`.

- [ ] **Step 3: Verify runtime health from fresh evidence**

Check that exactly one bot process exists and the latest log contains both the intelligence scheduler and ingestion scheduler startup records. Run `ingestion_cli health` and confirm no newly enabled source is immediately failing or suspended.

- [ ] **Step 4: Commit any final runbook clarification**

If the runbook required a circuit-breaker clarification, commit only that documentation:

```powershell
git add docs/operations/source-ingestion-runbook.md
git commit -m "docs: document ingestion circuit recovery"
```

If no runbook edit was necessary, do not create an empty commit.

- [ ] **Step 5: Report exact results**

Report:

- status totals across the 17 reviewed sources;
- the exact newly enabled source IDs;
- the exact disabled/authorization-required/denied/pending IDs and reasons;
- live-smoke success and failure counts;
- circuit-breaker threshold and manual recovery procedure;
- links to the audit report and registry;
- any unresolved evidence explicitly marked as unresolved.
