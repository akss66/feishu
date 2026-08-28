# Chinese Industry Media Direct Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Directly collect public, platform-relevant articles from 雨果跨境 and 网经社, analyze their temporary full text, and keep daily delivery independent from GDELT.

**Architecture:** Reuse the existing HTML collector and deterministic article gate. Add trusted, source-configured host/path scoping and platform-title prefiltering before detail requests, apply the full article gate after each detail response, register the two public media sources, and extend the existing seven-day media-body cleanup to direct full-text media.

**Tech Stack:** Python 3.12, asyncio, PyYAML, lxml, trafilatura, pytest, pytest-asyncio, existing `IngestionHttpClient` and SQLite repository.

## Global Constraints

- The only new sources are `media-cifnews-cross-border` and `media-100ec-cross-border`.
- The platform scope remains Amazon, TEMU, SHEIN, AliExpress, Shopee, eBay, Coupang, Ozon, Joybuy, and TikTok Shop.
- 雨果 uses `https://www.cifnews.com/`; 网经社 uses `https://imgs-b2b.100ec.cn/list--3--1.html`.
- Both sources use the existing HTML collector; production browser ingestion remains disabled.
- Collection runs at most once every 120 minutes, with limits of 10 雨果 details and 5 网经社 details per run.
- Only HTTPS, configured hosts, configured article paths, and platform-relevant candidates may be fetched.
- CAPTCHA, login, membership, paywall, JavaScript security-check, and 403 responses must fail safely; 429 uses the existing bounded `Retry-After` behavior and stops after exhaustion, without identity changes, proxies, or bypasses.
- Full media text is retained for exactly 7 days; Feishu receives analysis, a short evidence excerpt, and the original link rather than the full article.
- Media evidence remains `media`, never `official`.
- GDELT remains optional; its failure must not block direct media or the 09:00 daily report.
- No new dependency, credential, browser state, or secret is introduced.

---

## File Map

- `src/commerce_agent/ingestion/article_gate.py`: shared platform aliases, title relevance check, Chinese access-wall rejection, and full-body validation.
- `src/commerce_agent/ingestion/collectors/base.py`: resolve the trusted host allowlist from static source configuration.
- `src/commerce_agent/ingestion/collectors/html.py`: filter candidate paths/titles before detail requests and apply the article gate after detail responses.
- `src/commerce_agent/ingestion/registry.py`: validate the new HTML collector configuration fields.
- `src/commerce_agent/ingestion/service.py`: apply seven-day cleanup to direct full-text media as well as GDELT media bodies.
- `src/commerce_agent/sources/public_sources.yaml`: enable and scope the two reviewed media sources.
- `tests/unit/test_article_gate.py`: deterministic Chinese aliases and access-wall coverage.
- `tests/unit/test_industry_media_collectors.py`: focused HTML collector policy tests with fixed fixtures and fake HTTP.
- `tests/unit/test_source_registry.py`: exact source configuration and invalid-configuration tests.
- `tests/unit/test_ingestion_service.py`: direct-media retention test.
- `tests/fixtures/ingestion/cifnews_home.html`: representative public 雨果 article links plus excluded links.
- `tests/fixtures/ingestion/cifnews_article.html`: complete platform-relevant 雨果 article.
- `tests/fixtures/ingestion/100ec_list.html`: representative public 网经社 static-list links.
- `tests/fixtures/ingestion/100ec_article.html`: complete platform-relevant 网经社 article.
- `tests/fixtures/ingestion/100ec_challenge.html`: JavaScript security-check page that must be rejected.
- `tests/smoke/test_chinese_media_sources.py`: opt-in, two-request-per-source live acceptance.
- `docs/operations/ten-platform-source-acceptance.md`: live result and operating status.

---

### Task 1: Add Chinese Platform and Access-Wall Recognition

**Files:**
- Modify: `src/commerce_agent/ingestion/article_gate.py`
- Modify: `tests/unit/test_article_gate.py`

**Interfaces:**
- Consumes: `Platform` and normalized article/title text.
- Produces: `mentions_target_platform(text: str, platforms: tuple[Platform, ...]) -> bool`.
- Produces: `validate_public_article(...) -> None` with stable `ArticleGateError.code`.

- [ ] **Step 1: Write the failing Chinese alias tests**

Add these cases to `tests/unit/test_article_gate.py`:

```python
@pytest.mark.parametrize(
    ("platform", "mention"),
    [
        (Platform.AMAZON, "亚马逊卖家政策"),
        (Platform.SHEIN, "希音平台规则"),
        (Platform.ALIEXPRESS, "速卖通合规更新"),
        (Platform.COUPANG, "酷澎跨境卖家"),
        (Platform.TIKTOK_SHOP, "TTS 店铺治理"),
    ],
)
def test_article_gate_recognizes_controlled_chinese_platform_aliases(
    platform: Platform,
    mention: str,
) -> None:
    validate_public_article(
        body=article_html(f"{mention}发生变化，商家需要核查商品和账户。"),
        content_type="text/html",
        platforms=(platform,),
    )
```

- [ ] **Step 2: Run the alias tests and verify RED**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_article_gate.py::test_article_gate_recognizes_controlled_chinese_platform_aliases -q
```

Expected: FAIL with `article_platform_irrelevant`.

- [ ] **Step 3: Write the failing title prefilter and Chinese wall tests**

Add:

```python
from commerce_agent.ingestion.article_gate import mentions_target_platform


def test_title_prefilter_uses_the_same_platform_aliases_as_body_gate() -> None:
    assert mentions_target_platform(
        "亚马逊新规影响跨境卖家",
        (Platform.AMAZON,),
    )
    assert not mentions_target_platform(
        "本地体育赛事举行",
        (Platform.AMAZON, Platform.TEMU),
    )


@pytest.mark.parametrize(
    "marker",
    [
        "正在进行安全检查，请稍候",
        "请输入验证码后继续",
        "请登录后继续阅读",
        "会员专享内容",
        "付费阅读后查看全文",
    ],
)
def test_public_article_rejects_chinese_access_walls(marker: str) -> None:
    with pytest.raises(ArticleGateError, match="article_access_wall"):
        validate_public_article(
            body=article_html(f"亚马逊卖家注意：{marker}"),
            content_type="text/html",
            platforms=(Platform.AMAZON,),
        )
```

- [ ] **Step 4: Run the new tests and verify RED**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_article_gate.py -q
```

Expected: collection error because `mentions_target_platform` does not exist, then `article_access_wall` failures after the import is introduced.

- [ ] **Step 5: Implement the shared deterministic matcher**

In `article_gate.py`, replace direct alias matching with:

```python
_PLATFORM_ALIASES: dict[Platform, tuple[str, ...]] = {
    Platform.AMAZON: ("amazon", "亚马逊"),
    Platform.TEMU: ("temu",),
    Platform.SHEIN: ("shein", "希音"),
    Platform.ALIEXPRESS: ("aliexpress", "ali express", "速卖通"),
    Platform.SHOPEE: ("shopee",),
    Platform.EBAY: ("ebay",),
    Platform.COUPANG: ("coupang", "酷澎"),
    Platform.OZON: ("ozon",),
    Platform.JOYBUY: ("joybuy", "joy buy"),
    Platform.TIKTOK_SHOP: ("tiktok shop", "tik tok shop", "tts"),
}


def mentions_target_platform(
    text: str,
    platforms: tuple[Platform, ...],
) -> bool:
    folded = text.casefold()
    return any(
        alias in folded
        for platform in platforms
        for alias in _PLATFORM_ALIASES[platform]
    )
```

Extend `_ACCESS_WALL_MARKERS` with the five exact Chinese markers used by the test. Call `mentions_target_platform(visible_text, platforms)` inside `validate_public_article`.

- [ ] **Step 6: Run focused and regression tests**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_article_gate.py tests/unit/test_collectors.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```powershell
git add -- src/commerce_agent/ingestion/article_gate.py tests/unit/test_article_gate.py
git commit -m "feat: recognize Chinese marketplace media"
```

---

### Task 2: Scope Direct HTML Media Before and After Fetch

**Files:**
- Modify: `src/commerce_agent/ingestion/registry.py`
- Modify: `src/commerce_agent/ingestion/collectors/base.py`
- Modify: `src/commerce_agent/ingestion/collectors/html.py`
- Create: `tests/unit/test_industry_media_collectors.py`
- Create: `tests/fixtures/ingestion/cifnews_home.html`
- Create: `tests/fixtures/ingestion/cifnews_article.html`
- Create: `tests/fixtures/ingestion/100ec_list.html`
- Create: `tests/fixtures/ingestion/100ec_article.html`
- Create: `tests/fixtures/ingestion/100ec_challenge.html`

**Interfaces:**
- Consumes collector configuration:
  - `allowed_hosts: str`, comma-separated trusted hostnames;
  - `link_path_prefixes: str`, comma-separated absolute path prefixes;
  - `public_article_gate: bool`.
- Produces: `allowed_hosts(source: SourceDefinition) -> tuple[str, ...]`.
- Produces: HTML collection that never requests excluded paths, excluded titles, or excluded hosts.
- Produces stable failures using the existing `CollectedFailure` and `ArticleGateError.code`.

- [ ] **Step 1: Create minimal fixed HTML fixtures**

Create `cifnews_home.html`:

```html
<!doctype html><html><body>
  <a href="/article/187800">亚马逊新规要求卖家检查商品图片</a>
  <a href="/article/187801">本地体育赛事举行</a>
  <a href="/activity/900">TikTok 招商活动</a>
  <a href="https://ads.example/landing">Amazon 广告</a>
</body></html>
```

Create `cifnews_article.html`:

```html
<!doctype html><html><head>
  <title>亚马逊新规要求卖家检查商品图片</title>
  <meta property="article:published_time" content="2026-07-27T08:00:00+08:00">
</head><body><article>
  <p>亚马逊发布新的商品图片合规要求，跨境卖家需要核查当前在售商品，确认图片与商品实际信息一致，并保留内部复核记录。</p>
  <p>本次调整可能影响商品编辑、广告素材和团队审核流程。卖家应先查看亚马逊官方通知，再根据生效站点和时间安排处理顺序。</p>
  <p>建议运营团队导出受影响商品，按风险等级检查主图、附图和文字说明；在官方规则没有确认前，不要仅凭媒体报道批量修改。</p>
  <p>该报道属于行业媒体信息，不等同于亚马逊官方公告。实际执行仍应以卖家后台、官方帮助中心和对应站点通知为准。</p>
  <p>负责人可以先抽样检查高销量商品，再评估是否需要扩大范围。对于不同国家站点，应分别记录规则链接，避免把一个站点的要求错误套用到全球。</p>
  <p>如果商品由多个团队共同维护，应统一变更窗口和回滚方式。任何批量操作都要先备份当前字段，确保发现误判后能够恢复。</p>
  <p>日报中的风险等级只用于安排复核优先级，不代替法律意见或平台裁决。卖家最终动作必须建立在可追溯的官方依据上。</p>
</article></body></html>
```

Create `100ec_list.html`:

```html
<!doctype html><html><body>
  <a href="/detail--6659472.html">TEMU 合规政策收紧</a>
  <a href="/report/">跨境报告库</a>
  <a href="/detail--6659473.html">国内零售企业动态</a>
</body></html>
```

Create `100ec_article.html`:

```html
<!doctype html><html><head>
  <title>TEMU 合规政策收紧</title>
  <meta property="article:published_time" content="2026-07-27T09:00:00+08:00">
</head><body><article>
  <p>TEMU 平台相关合规要求出现变化，跨境商家需要重点检查商品资质、标签信息和申报材料，避免因资料不一致影响销售。</p>
  <p>报道提到的变化可能涉及多个类目和站点，因此卖家不应直接把媒体描述当作最终规则，而要进一步核对 TEMU 卖家后台公告。</p>
  <p>建议团队建立待核实清单，记录涉及站点、类目、生效时间和官方依据；确认后再分批修改商品，保留变更前后的审计记录。</p>
  <p>如果官方后台尚未发布一致说明，应把该信息标记为媒体线索，并设置复核负责人，而不是立即执行不可逆的批量操作。</p>
  <p>对于高风险类目，可以优先检查证书有效期、责任主体和包装标签。不同站点的材料要求可能不同，不能使用同一份结论覆盖全部市场。</p>
  <p>运营、合规和供应链团队应共享同一份问题清单，并记录每项结论的来源。没有找到官方原文的事项需要继续保持待核实状态。</p>
  <p>该媒体信息用于发现问题和安排调查顺序，不代表 TEMU 已正式确认全部细节。最终执行应以卖家后台和平台书面通知为准。</p>
</article></body></html>
```

Create `100ec_challenge.html`:

```html
<!doctype html><html><body>
  <script>document.cookie = "HW_CHECK=test"; location.reload();</script>
  <p>正在进行安全检查，请稍候...</p>
</body></html>
```

- [ ] **Step 2: Write the failing pre-fetch policy tests**

In `test_industry_media_collectors.py`, define a small `FakeHttpPort` that records every `FetchRequest`, returns fixed `FetchResponse` objects, and never resolves real DNS. Add:

```python
async def test_cifnews_fetches_only_scoped_platform_article() -> None:
    source = direct_media_source(
        source_id="media-cifnews-cross-border",
        entry_url="https://www.cifnews.com/",
        platforms=(Platform.AMAZON,),
        config={
            "link_selector": "a",
            "link_path_prefixes": "/article/",
            "allowed_hosts": "www.cifnews.com",
            "public_article_gate": True,
            "item_limit": 10,
        },
    )
    http = FakeHttpPort(
        {
            source.entry_url: fixture_response("cifnews_home.html", source.entry_url),
            "https://www.cifnews.com/article/187800": fixture_response(
                "cifnews_article.html",
                "https://www.cifnews.com/article/187800",
            ),
        }
    )

    items = await collect_items(HtmlCollector(http), source)

    assert [request.url for request in http.requests] == [
        source.entry_url,
        "https://www.cifnews.com/article/187800",
    ]
    assert len(items) == 1
```

The test helper must build a `TrustTier.MEDIA`, `ContentScope.FULL_TEXT`, `ComplianceStatus.ALLOWED` source with complete attribution and publisher identity.

- [ ] **Step 3: Run the pre-fetch test and verify RED**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_industry_media_collectors.py::test_cifnews_fetches_only_scoped_platform_article -q
```

Expected: FAIL because the collector currently requests irrelevant matching links and the registry/config policy does not exist.

- [ ] **Step 4: Write the failing post-fetch gate tests**

Add:

```python
async def test_100ec_challenge_is_not_emitted_as_full_text() -> None:
    source = direct_media_source(
        source_id="media-100ec-cross-border",
        entry_url="https://imgs-b2b.100ec.cn/list--3--1.html",
        platforms=(Platform.TEMU,),
        config={
            "link_selector": "a",
            "link_path_prefixes": "/detail--",
            "allowed_hosts": "imgs-b2b.100ec.cn",
            "public_article_gate": True,
            "item_limit": 5,
        },
    )
    http = FakeHttpPort(
        {
            source.entry_url: fixture_response("100ec_list.html", source.entry_url),
            "https://imgs-b2b.100ec.cn/detail--6659472.html": fixture_response(
                "100ec_challenge.html",
                "https://imgs-b2b.100ec.cn/detail--6659472.html",
            ),
        }
    )

    results = await collect_results(HtmlCollector(http), source)

    assert not any(isinstance(result, CollectedItem) for result in results)
    assert [
        result.error_code
        for result in results
        if isinstance(result, CollectedFailure)
    ] == [
        "article_access_wall"
    ]
```

Add a successful 100ec test using `100ec_article.html`, and an allowed-host test proving that a matching `https://www.100ec.cn/detail--1.html` candidate is rejected before an HTTP request.

- [ ] **Step 5: Run the new collector tests and verify RED**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_industry_media_collectors.py -q
```

Expected: FAIL because path/title filtering, configured hosts, and the public article gate are not wired into `HtmlCollector`.

- [ ] **Step 6: Add strict registry parsing**

Allow only these new fields for `CollectorKind.HTML`:

```python
{
    "link_selector",
    "article_selector",
    "item_limit",
    "allowed_hosts",
    "link_path_prefixes",
    "public_article_gate",
}
```

Parse `public_article_gate` with `_require_bool`. Parse the comma-separated fields as non-empty strings, then validate:

- every `allowed_hosts` token is a normalized DNS hostname without a scheme, port, path, wildcard, localhost, private IP, or metadata host;
- the source entry hostname is included;
- every `link_path_prefixes` token starts with `/`, contains no query/fragment, and is not `/`.

Reject invalid configuration with stable `SourceRegistryError` messages that do not echo untrusted page content.

- [ ] **Step 7: Implement configured host and path helpers**

In `collectors/base.py`, change `allowed_hosts` to:

```python
def allowed_hosts(source: SourceDefinition) -> tuple[str, ...]:
    configured = source.collector_config.get("allowed_hosts")
    if isinstance(configured, str):
        return tuple(
            dict.fromkeys(
                token.strip().rstrip(".").lower()
                for token in configured.split(",")
                if token.strip()
            )
        )
    host = urlsplit(source.entry_url).hostname
    if host is None:
        raise CollectorError("invalid_config")
    return (host.rstrip(".").lower(),)
```

Add a private HTML helper that returns the configured path-prefix tuple and checks `urlsplit(candidate.url).path.startswith(prefix)`.

Extend `links_from_html` with an optional predicate so rejected links do not consume the item limit:

```python
def links_from_html(
    body: bytes,
    *,
    base_url: str,
    selector: str,
    limit: int,
    candidate_filter: Callable[[CollectedItem], bool] | None = None,
) -> list[CollectedItem]:
```

After constructing a candidate but before adding it to `seen` or `items`, skip it when `candidate_filter` returns false. Existing callers omit the argument and retain current behavior.

- [ ] **Step 8: Implement pre-fetch title/path filtering and post-fetch gating**

In `HtmlCollector.collect`:

1. construct one `candidate_filter` that compares `urlsplit(candidate.url).hostname` with `allowed_hosts(source)`, checks the configured path prefixes, and—when `public_article_gate` is true—calls `mentions_target_platform` on the candidate title;
2. pass that predicate into `links_from_html`, so excluded links do not consume the source item limit;
3. fetch accepted details through `fetch_request`, which independently reapplies the configured host allowlist;
4. call `validate_public_article` on the detail body and content type;
5. convert `ArticleGateError` into `CollectedFailure(error.code)` and continue.

Do not catch `asyncio.CancelledError`. Do not retry or change headers after a gate rejection.

- [ ] **Step 9: Run focused, registry, and security regressions**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_industry_media_collectors.py tests/unit/test_article_gate.py tests/unit/test_source_registry.py tests/unit/test_http_safety.py tests/unit/test_collectors.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit Task 2**

```powershell
git add -- src/commerce_agent/ingestion/registry.py src/commerce_agent/ingestion/collectors/base.py src/commerce_agent/ingestion/collectors/html.py tests/unit/test_industry_media_collectors.py tests/fixtures/ingestion/cifnews_home.html tests/fixtures/ingestion/cifnews_article.html tests/fixtures/ingestion/100ec_list.html tests/fixtures/ingestion/100ec_article.html tests/fixtures/ingestion/100ec_challenge.html
git commit -m "feat: gate direct industry media collection"
```

---

### Task 3: Register and Enable the Two Direct Sources

**Files:**
- Modify: `src/commerce_agent/sources/public_sources.yaml`
- Modify: `tests/unit/test_source_registry.py`

**Interfaces:**
- Consumes the Task 2 HTML config fields.
- Produces two enabled, allowed, full-text `TrustTier.MEDIA` sources.

- [ ] **Step 1: Write failing exact registry assertions**

Add:

```python
def test_public_registry_enables_scoped_chinese_media_sources() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    cifnews = registry.require("media-cifnews-cross-border")
    ec100 = registry.require("media-100ec-cross-border")

    assert (
        cifnews.compliance,
        cifnews.enabled,
        cifnews.collector,
        cifnews.content_scope,
        cifnews.publisher_key,
    ) == (
        ComplianceStatus.ALLOWED,
        True,
        CollectorKind.HTML,
        ContentScope.FULL_TEXT,
        "cifnews.com",
    )
    assert cifnews.collector_config == {
        "link_selector": "a",
        "link_path_prefixes": "/article/",
        "allowed_hosts": "www.cifnews.com",
        "public_article_gate": True,
        "item_limit": 10,
    }

    assert (
        ec100.compliance,
        ec100.enabled,
        ec100.collector,
        ec100.content_scope,
        ec100.publisher_key,
    ) == (
        ComplianceStatus.ALLOWED,
        True,
        CollectorKind.HTML,
        ContentScope.FULL_TEXT,
        "100ec.cn",
    )
    assert ec100.entry_url == "https://imgs-b2b.100ec.cn/list--3--1.html"
    assert ec100.collector_config == {
        "link_selector": "a",
        "link_path_prefixes": "/detail--",
        "allowed_hosts": "imgs-b2b.100ec.cn",
        "public_article_gate": True,
        "item_limit": 5,
    }
```

- [ ] **Step 2: Run the registry test and verify RED**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_source_registry.py::test_public_registry_enables_scoped_chinese_media_sources -q
```

Expected: FAIL because both sources are disabled metadata candidates.

- [ ] **Step 3: Update the source definitions**

Set 雨果 to:

```yaml
source_id: media-cifnews-cross-border
name: 雨果跨境
entry_url: https://www.cifnews.com/
collector: html
content_scope: full_text
attribution: 雨果跨境
publisher_key: cifnews.com
compliance: allowed
enabled: true
interval_minutes: 120
reviewed_at: 2026-07-27
collector_config:
  link_selector: a
  link_path_prefixes: /article/
  allowed_hosts: www.cifnews.com
  public_article_gate: true
  item_limit: 10
```

Set 网经社 to:

```yaml
source_id: media-100ec-cross-border
name: 网经社跨境电商台
entry_url: https://imgs-b2b.100ec.cn/list--3--1.html
collector: html
content_scope: full_text
attribution: 网经社跨境电商台
publisher_key: 100ec.cn
compliance: allowed
enabled: true
interval_minutes: 120
reviewed_at: 2026-07-27
collector_config:
  link_selector: a
  link_path_prefixes: /detail--
  allowed_hosts: imgs-b2b.100ec.cn
  public_article_gate: true
  item_limit: 5
```

Update compliance notes with the exact public entry, the low-frequency limits, no-login/no-bypass rule, and the controlled smoke requirement. Remove inherited metadata-only values from these two concrete definitions.

- [ ] **Step 4: Add invalid-config registry tests**

Parameterize invalid `allowed_hosts` values (`*`, `localhost`, `https://host`, `host:443`) and invalid path prefixes (`/`, `detail--`, `/detail--?p=1`). Assert `SourceRegistryError` for every value.

- [ ] **Step 5: Run registry and coverage tests**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_source_registry.py tests/unit/test_ingestion_cli.py tests/unit/test_intelligence_reports.py -q
```

Expected: PASS, with the two new sources counted under the ten platforms they cover.

- [ ] **Step 6: Commit Task 3**

```powershell
git add -- src/commerce_agent/sources/public_sources.yaml tests/unit/test_source_registry.py
git commit -m "feat: enable direct Chinese media sources"
```

---

### Task 4: Apply Seven-Day Retention to Direct Media Bodies

**Files:**
- Modify: `src/commerce_agent/ingestion/service.py`
- Modify: `tests/unit/test_ingestion_service.py`

**Interfaces:**
- Consumes `SourceDefinition.trust_tier`, `content_scope`, and `adapter`.
- Produces seven-day snapshot pruning and repository body redaction for:
  - every GDELT source;
  - every direct `TrustTier.MEDIA` + `ContentScope.FULL_TEXT` source.

- [ ] **Step 1: Write the failing direct-media retention test**

Add `ContentScope` to the existing import from `commerce_agent.ingestion.models`, then add:

```python
async def test_direct_full_text_media_expires_bodies_after_seven_days() -> None:
    direct_media = replace(
        source("media-cifnews-cross-border", collector=CollectorKind.HTML),
        trust_tier=TrustTier.MEDIA,
        content_scope=ContentScope.FULL_TEXT,
        publisher_key="cifnews.com",
        attribution="雨果跨境",
    )
    ingestion, repository, snapshots = service(
        [direct_media],
        {CollectorKind.HTML: FakeCollector()},
    )

    await ingestion.run_source(direct_media.source_id)

    cutoff = NOW - timedelta(days=7)
    assert snapshots.pruned == [(direct_media.source_id, cutoff)]
    assert repository.media_redactions == [((direct_media.source_id,), cutoff)]
```

Keep the existing GDELT retention test unchanged.

- [ ] **Step 2: Run the retention tests and verify RED**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_ingestion_service.py -k "expires_media_bodies or direct_full_text_media" -q
```

Expected: the direct-media test FAILS with empty `pruned` and `media_redactions`.

- [ ] **Step 3: Implement a named retention predicate**

Add:

```python
def _uses_temporary_media_body(source: SourceDefinition) -> bool:
    return source.adapter is SourceAdapter.GDELT or (
        source.trust_tier is TrustTier.MEDIA
        and source.content_scope is ContentScope.FULL_TEXT
    )
```

Use it in place of `source.adapter is SourceAdapter.GDELT` at the cleanup call site. Retain the existing `gdelt_media_body_retention_days` setting name for backward-compatible environment configuration, but document in the constructor comment that the seven-day value governs all temporary media bodies.

- [ ] **Step 4: Run service and persistence regressions**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_ingestion_service.py tests/integration/test_intelligence_repository.py tests/integration/test_ingestion_pipeline.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```powershell
git add -- src/commerce_agent/ingestion/service.py tests/unit/test_ingestion_service.py
git commit -m "feat: expire direct media article bodies"
```

---

### Task 5: Add Bounded Live Smoke Tests

**Files:**
- Create: `tests/smoke/test_chinese_media_sources.py`
- Modify only if live evidence requires selector correction: `src/commerce_agent/sources/public_sources.yaml`

**Interfaces:**
- Consumes the real registry and existing safe HTTP client.
- Produces one opt-in live result per source with at most one list request and one detail request.

- [ ] **Step 1: Write the opt-in smoke harness**

Create a test guarded by:

```python
@pytest.mark.skipif(
    os.getenv("RUN_CHINESE_MEDIA_SMOKE") != "1",
    reason="set RUN_CHINESE_MEDIA_SMOKE=1 to run controlled Chinese-media checks",
)
```

For each source:

1. load it from the production registry;
2. replace `item_limit` with `1` without mutating the registry object;
3. construct `IngestionHttpClient` with Cloudflare DoH, concurrency `1`, domain RPS `0.5`, timeout `20`, `max_retries=0`, `max_redirects=0`, and response limit `2_000_000`;
4. wrap it in a request budget that raises after two requests;
5. run `HtmlCollector`;
6. assert exactly one `CollectedItem`, HTTPS original URL, non-empty body, and no more than two HTTP requests;
7. run `ContentExtractor` and assert `metadata["content_scope"] == "full_text"` and the expected publisher key.

- [ ] **Step 2: Run smoke tests without the flag**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/smoke/test_chinese_media_sources.py -q
```

Expected: one or more SKIPPED tests and no network request.

- [ ] **Step 3: Run exactly one controlled live smoke**

Run:

```powershell
$env:RUN_CHINESE_MEDIA_SMOKE='1'
..\..\.venv\Scripts\python.exe -m pytest tests/smoke/test_chinese_media_sources.py -q --tb=short
$smokeExit = $LASTEXITCODE
Remove-Item Env:RUN_CHINESE_MEDIA_SMOKE
exit $smokeExit
```

Expected: PASS for each source. If a source returns 403, 429, challenge content, empty candidates, or an extraction failure, record that exact result and stop. Do not retry, change identity, use a proxy, or weaken the gate.

- [ ] **Step 4: Correct only evidence-backed selector/path mistakes**

If the list is public and reachable but the fixed selector/path is wrong:

1. add a fixture reproducing the observed public markup;
2. write a failing unit test for the corrected selector/path;
3. run it and verify RED;
4. make the smallest source-config or parser correction;
5. run the unit test and verify GREEN;
6. perform at most one additional live smoke for that corrected source.

Do not use this step for access-control failures.

- [ ] **Step 5: Commit smoke coverage**

```powershell
git add -- tests/smoke/test_chinese_media_sources.py src/commerce_agent/sources/public_sources.yaml tests/fixtures/ingestion tests/unit/test_industry_media_collectors.py
git commit -m "test: verify bounded Chinese media collection"
```

---

### Task 6: Document Operations and Verify the Whole Robot

**Files:**
- Modify: `docs/operations/ten-platform-source-acceptance.md`
- Modify if source-status copy needs it: `README.md`

**Interfaces:**
- Consumes completed unit, integration, smoke, and lint output.
- Produces an operator-readable source status with failure behavior and no unsupported stability claims.

- [ ] **Step 1: Record exact acceptance evidence**

Add a dated section containing:

- each source URL;
- status code;
- request count;
- candidate count;
- extracted-document count;
- whether the article gate accepted or rejected;
- exact stable failure code when rejected;
- confirmation that no login, CAPTCHA action, proxy, alternate identity, or browser state was used;
- confirmation that GDELT is optional and its 429 does not block these sources.

- [ ] **Step 2: Run the complete offline suite**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m pytest -q
```

Expected: PASS with only explicitly opt-in live tests skipped.

- [ ] **Step 3: Run lint and diff safety checks**

Run:

```powershell
..\..\.venv\Scripts\python.exe -m ruff check src tests
git diff --check
git status --short
```

Expected: Ruff PASS, no whitespace errors, and only intended files changed.

- [ ] **Step 4: Check for staged secrets before final commit**

Run:

```powershell
git diff --cached --name-only
git diff --cached | Select-String -Pattern 'sk-[A-Za-z0-9]|app_secret|api_key\\s*=|password\\s*=' -CaseSensitive:$false
```

Expected: no real secret value.

- [ ] **Step 5: Commit operations evidence**

```powershell
git add -- docs/operations/ten-platform-source-acceptance.md README.md
git commit -m "docs: operate direct Chinese media sources"
```

- [ ] **Step 6: Final evidence summary**

Report:

- commits created;
- offline test count and skips;
- live status of each source;
- actual enabled/disabled status;
- whether a real full-text item reached extraction;
- any external limitation;
- the exact command for one manual ingestion and one Feishu preview/send.
