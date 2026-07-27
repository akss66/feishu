# Ten-Platform Data Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不扩展平台范围、不绕过网站限制的前提下，让本机飞书机器人对 Amazon、TEMU、SHEIN、AliExpress、Shopee、eBay、Coupang、Ozon、Joybuy 和 TikTok Shop 建立可审计的数据覆盖、正文分级、AI 分析和 09:00 日报链路。

**Architecture:** 保留现有模块化单体、SQLite、APScheduler、Outbox 和 DeepSeek 分析链路。先把每条材料的内容等级变成强制数据合同，并让分析器只消费 `full_text`；再通过来源注册表、官方通知入口和发现层扩展十个平台。覆盖统计由“启用数量”升级为“有效来源、内容等级、健康状态和当日更新”四类指标，08:40 预采集与 09:00 幂等发送通过独立编排器衔接。

**Tech Stack:** Python 3.11–3.12、SQLAlchemy asyncio、SQLite、APScheduler 3.x、httpx/httpcore、PyYAML、Trafilatura、Pydantic、lark-channel-sdk、pytest、Ruff。

## Global Constraints

- 平台范围固定为 `amazon`、`temu`、`shein`、`aliexpress`、`shopee`、`ebay`、`coupang`、`ozon`、`joybuy`、`tiktok_shop`；不得注册 Lazada、Mercado Libre 或其他平台。
- 每个平台的交付目标是两个有效来源，优先为一个官方来源和一个独立权威媒体来源。
- 只有 `content_scope=full_text` 可以创建或领取 AI 分析任务；`feed_summary` 只生成“待核实线索”，`metadata_only` 只用于发现和去重。
- 第一版不购买媒体服务，但必须提供稳定的 `LicensedNewsProvider` 协议与契约测试。
- 不使用登录 Cookie、浏览器会话、验证码绕过、代理换身份或自动批量读取微信公众号历史文章。
- 公众账号与卖家后台通知只通过官方 API/Webhook、专用邮箱或已绑定飞书群的人工提交进入系统。
- 人工提交和邮件不得把订单、余额、买家、店铺处罚、邮箱、手机号或凭据写入 LLM 请求、日志或日报。
- 单一来源失败必须隔离；08:55 后不再等待慢来源，09:00 继续幂等发送。
- 日报窗口保持“前一日 09:00（含）到当日 09:00（不含）”；迟到材料进入下一份日报。
- 本机运行要求保持不变：电脑开机、用户登录、网络可用；本期不迁移云服务器。
- 真实来源只有在许可证据、离线样本和单次受控冒烟均通过后才能改为 `allowed + enabled=true`。
- 设计依据：`docs/superpowers/specs/2026-07-27-data-coverage-expansion-design.md`。

---

## File Structure

新增和调整的文件按单一职责划分：

- `src/commerce_agent/ingestion/models.py`：材料等级、通知来源和覆盖状态的领域类型。
- `src/commerce_agent/ingestion/registry.py`：YAML 来源合同与十平台范围校验。
- `src/commerce_agent/ingestion/official_notices.py`：官方通知标准对象、白名单、脱敏和隐私拒绝。
- `src/commerce_agent/ingestion/manual_submissions.py`：飞书文本提交解析与入库服务。
- `src/commerce_agent/ingestion/email_notices.py`：专用邮箱轮询适配器，不含日报或 LLM 逻辑。
- `src/commerce_agent/ingestion/providers.py`：`OfficialNoticeProvider` 与 `LicensedNewsProvider` 协议。
- `src/commerce_agent/ingestion/pre_report.py`：08:40–08:59 的预采集、分析和预览编排。
- `src/commerce_agent/persistence/models.py`：来源内容政策、通知审计和现有文档出处表。
- `src/commerce_agent/persistence/ingestion.py`：内容政策同步、通知入库和内容等级持久化。
- `src/commerce_agent/intelligence/repository.py`：严格正文领取、线索计数、健康覆盖和异常查询。
- `src/commerce_agent/intelligence/reports.py`：`2/2` 覆盖、线索数、来源异常和用户可读卡片。
- `src/commerce_agent/intelligence/scheduler.py`：08:40 预处理与 09:00 发送两个独立作业。
- `src/commerce_agent/sources/public_sources.yaml`：仅十个平台的公开来源与候选状态。
- `src/commerce_agent/sources/official_accounts.yaml`：人工提交允许的官方账号、域名和平台映射。
- `docs/operations/ten-platform-source-acceptance.md`：逐来源许可证据、冒烟结果和上线门。

## Dependency Graph

```text
Task 1 内容等级合同
  -> Task 2 持久化与 LLM 严格门禁
  -> Task 3 覆盖统计

Task 4 十平台公开来源与发现层
Task 5 人工官方通知入口
  -> Task 6 专用邮箱通知入口

Task 2 + Task 4 + Task 5 + Task 6
  -> Task 7 跨入口去重与多平台归因
  -> Task 8 08:40 预处理与 09:00 发送
  -> Task 9 日报覆盖和异常展示

Task 1 + Task 2
  -> Task 10 付费接口预留与整体验收
```

### Task 1: 强制材料内容等级与十平台来源合同

**Files:**
- Modify: `src/commerce_agent/ingestion/models.py`
- Modify: `src/commerce_agent/ingestion/registry.py`
- Modify: `src/commerce_agent/sources/public_sources.yaml`
- Modify: `tests/fixtures/ingestion/valid_sources.yaml`
- Modify: `tests/unit/test_source_registry.py`

**Interfaces:**
- Consumes: 现有 `SourceDefinition`、`ContentScope`、`Platform`。
- Produces: `SourceDefinition.content_scope: ContentScope`、`SourceDefinition.publisher_key: str`、`SourceDefinition.attribution: str`；所有启用来源均具备完整材料政策。

- [ ] **Step 1: 写出启用来源必须声明内容等级的失败测试**

```python
def test_enabled_source_requires_material_policy(tmp_path: Path) -> None:
    document = _valid_document()
    source = document["sources"][0]
    source["enabled"] = True
    for field in ("content_scope", "publisher_key", "attribution"):
        source.pop(field, None)

    with pytest.raises(SourceRegistryError, match="content_scope"):
        SourceRegistry.from_yaml(_write_registry(tmp_path, document))
```

- [ ] **Step 2: 写出平台枚举不得漂移的失败测试**

```python
def test_public_registry_is_limited_to_original_ten_platforms() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    assert {platform.value for platform in Platform} == {
        "amazon", "temu", "shein", "aliexpress", "shopee",
        "ebay", "coupang", "ozon", "joybuy", "tiktok_shop",
    }
    assert {platform for source in registry.sources for platform in source.platforms} <= set(Platform)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_source_registry.py -q`

Expected: FAIL，因为官方启用来源仍允许缺少 `content_scope`、`publisher_key` 和 `attribution`。

- [ ] **Step 4: 收紧注册表合同**

在 `_parse_source()` 完成枚举解析后调用统一验证器：

```python
def _validate_material_policy(
    *,
    enabled: bool,
    content_scope: ContentScope | None,
    publisher_key: str | None,
    attribution: str | None,
    context: str,
) -> None:
    if not enabled:
        return
    if content_scope is None:
        raise SourceRegistryError(f"{context}: enabled source requires content_scope")
    if publisher_key is None:
        raise SourceRegistryError(f"{context}: enabled source requires publisher_key")
    if attribution is None:
        raise SourceRegistryError(f"{context}: enabled source requires attribution")
```

移除“启用媒体一律拒绝 `full_text`”的旧规则，改为：只有 `compliance=allowed`、出版机构目录允许全文访问、且注册表明确标记 `full_text` 的来源才允许启用。

- [ ] **Step 5: 给现有启用来源标记真实内容等级**

使用以下规则更新 YAML：

```yaml
content_scope: feed_summary  # RSS 只返回摘要时
publisher_key: amazon.com
attribution: Amazon
```

```yaml
content_scope: full_text  # HTML 详情页实际保存完整正文时
publisher_key: ebayinc.com
attribution: eBay Inc.
```

Amazon SP-API RSS 与 eBay Newsroom RSS 先标记 `feed_summary`；eBay Press Room、Coupang Seller University 和 Joybuy Newsroom 只有离线样本证明详情正文完整时标记 `full_text`。

- [ ] **Step 6: 运行定向测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_source_registry.py tests/unit/test_content_extraction.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```powershell
git add -- src/commerce_agent/ingestion/models.py src/commerce_agent/ingestion/registry.py src/commerce_agent/sources/public_sources.yaml tests/fixtures/ingestion/valid_sources.yaml tests/unit/test_source_registry.py
git commit -m "feat: require explicit source content policy"
```

### Task 2: 持久化所有材料政策并仅分析完整正文

**Files:**
- Modify: `src/commerce_agent/ingestion/extract.py`
- Modify: `src/commerce_agent/ingestion/service.py`
- Modify: `src/commerce_agent/persistence/models.py`
- Modify: `src/commerce_agent/persistence/ingestion.py`
- Modify: `src/commerce_agent/intelligence/repository.py`
- Modify: `tests/integration/test_ingestion_repository.py`
- Modify: `tests/integration/test_intelligence_repository.py`
- Modify: `tests/integration/test_intelligence_pipeline.py`

**Interfaces:**
- Consumes: Task 1 的非空 `SourceDefinition.content_scope`、`publisher_key`、`attribution`。
- Produces: `SourceMaterialPolicy` 一对一表；每个新旧文档版本均可幂等补齐
  `DocumentProvenance`；`SqlAlchemyIntelligenceRepository.claim_next()` 只返回
  `content_scope == "full_text"`。

- [ ] **Step 1: 写出摘要不得领取分析任务的失败测试**

```python
async def test_claim_next_only_returns_full_text(tmp_path) -> None:
    await _persist_scoped_version(tmp_path, source_id="summary", scope="feed_summary")
    full_version = await _persist_scoped_version(tmp_path, source_id="full", scope="full_text")

    claim = await repository.claim_next(now=NOW)

    assert claim is not None
    assert claim.document_version_id == full_version
    assert claim.content_scope == "full_text"
```

- [ ] **Step 2: 写出重复版本可补齐出处的失败测试**

```python
async def test_reingestion_backfills_missing_provenance(tmp_path) -> None:
    first = await repository.persist_version(_candidate_without_provenance())
    second = await repository.persist_version(
        replace(
            _candidate_without_provenance(),
            publisher_key="ebayinc.com",
            attribution="eBay Inc.",
            content_scope="full_text",
        )
    )
    assert second.version_id == first.version_id
    assert await _stored_scope(second.version_id) == "full_text"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_ingestion_repository.py tests/integration/test_intelligence_repository.py -q`

Expected: FAIL，因为出处只在 `created_version=True` 时写入，领取条件仍允许 `feed_summary`。

- [ ] **Step 4: 让提取器对官方和媒体统一生成材料政策**

用统一函数替换 `_media_provenance()`：

```python
def _material_policy(source: SourceDefinition, item: CollectedItem) -> dict[str, str]:
    scope = item.content_scope or source.content_scope
    publisher_key = item.publisher_key or source.publisher_key
    if scope is None or publisher_key is None or source.attribution is None:
        raise ExtractionError("missing_material_policy")
    return {
        "publisher_key": publisher_key,
        "attribution": source.attribution,
        "content_scope": scope.value,
    }
```

- [ ] **Step 5: 新增无破坏的来源材料政策表**

```python
class SourceMaterialPolicy(Base):
    __tablename__ = "source_material_policies"

    source_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    publisher_key: Mapped[str] = mapped_column(String(253), nullable=False)
    attribution: Mapped[str] = mapped_column(Text, nullable=False)
    content_scope: Mapped[str] = mapped_column(String(32), nullable=False)
```

`sync_sources()` 对拥有完整材料政策的来源执行 upsert，对政策不完整的禁用候选不创建行。
`Database.create_schema()` 只新增表，不修改现有 `sources` 或文档表。

- [ ] **Step 6: 在重复版本上幂等插入出处**

把 `DocumentProvenance` 的插入移出 `if created_version:`，使用 SQLite 冲突忽略：

```python
if candidate.publisher_key is not None:
    await session.execute(
        sqlite_insert(DocumentProvenance)
        .values(
            document_version_id=version_id,
            publisher_key=candidate.publisher_key,
            attribution=candidate.attribution,
            content_scope=candidate.content_scope,
        )
        .on_conflict_do_nothing(index_elements=["document_version_id"])
    )
```

若既有出处与本次政策不一致，抛出 `ValueError("document_provenance_conflict")`，不得静默改写历史材料等级。

- [ ] **Step 7: 把分析领取条件改为严格等于完整正文**

```python
.join(
    DocumentProvenance,
    DocumentProvenance.document_version_id == DocumentVersion.id,
)
.where(DocumentProvenance.content_scope == ContentScope.FULL_TEXT.value)
```

`backfill_jobs()` 可以继续为所有版本建立作业，但摘要和元数据作业永远不会被领取；健康查询要把这些作业分类为 `ineligible`，而不是显示为积压失败。

- [ ] **Step 8: 运行定向测试**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_ingestion_repository.py tests/integration/test_intelligence_repository.py tests/integration/test_intelligence_pipeline.py -q`

Expected: PASS，且模拟 LLM 对 `feed_summary` 和 `metadata_only` 的调用次数均为 0。

- [ ] **Step 9: 提交**

```powershell
git add -- src/commerce_agent/ingestion/extract.py src/commerce_agent/ingestion/service.py src/commerce_agent/persistence/models.py src/commerce_agent/persistence/ingestion.py src/commerce_agent/intelligence/repository.py tests/integration/test_ingestion_repository.py tests/integration/test_intelligence_repository.py tests/integration/test_intelligence_pipeline.py
git commit -m "fix: analyze only verified full text"
```

### Task 3: 建立严格的 `2/2` 覆盖和内容等级统计

**Files:**
- Modify: `src/commerce_agent/intelligence/reports.py`
- Modify: `src/commerce_agent/intelligence/repository.py`
- Modify: `tests/integration/test_intelligence_repository.py`
- Modify: `tests/unit/test_intelligence_reports.py`

**Interfaces:**
- Consumes: `Source`、`SourceHealth`、`SourcePlatform`、`DocumentProvenance`、`DocumentVersion`。
- Produces: 扩展后的 `CoverageRow`：

```python
@dataclass(frozen=True, slots=True)
class CoverageRow:
    platform: Platform
    effective_source_count: int
    target_source_count: int
    full_text_update_count: int
    feed_summary_count: int
    metadata_only_count: int
    source_anomalies: tuple[str, ...] = ()
```

- [ ] **Step 1: 写出 `2/2` 与三种内容等级的失败测试**

```python
async def test_coverage_counts_effective_sources_and_content_scopes(tmp_path) -> None:
    rows = await repository.list_coverage(window_start=START, window_end=END)
    amazon = next(row for row in rows if row.platform is Platform.AMAZON)
    assert amazon.effective_source_count == 1
    assert amazon.target_source_count == 2
    assert amazon.full_text_update_count == 1
    assert amazon.feed_summary_count == 2
    assert amazon.metadata_only_count == 0
```

- [ ] **Step 2: 写出连续失败来源不得计为有效来源的失败测试**

```python
async def test_suspended_source_is_not_effective_coverage(tmp_path) -> None:
    await _set_health("amazon-media", status="suspended", failures=3)
    row = await _coverage_for(Platform.AMAZON)
    assert row.effective_source_count == 1
    assert "amazon-media：连续失败 3 次，已暂停" in row.source_anomalies
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_intelligence_repository.py tests/unit/test_intelligence_reports.py -q`

Expected: FAIL，因为当前 `CoverageRow` 只有启用来源数和已验证更新数。

- [ ] **Step 4: 实现有效来源 SQL 语义**

有效来源只计入同时满足以下条件的唯一 `source_id`：

```text
Source.enabled = true
Source.compliance = "allowed"
SourceMaterialPolicy.content_scope = "full_text"
SourceHealth.health_status IN ("healthy", "unknown")
SourceHealth.consecutive_failures < 3
```

未产生过文档但已完成一次成功空采集的来源，使用 `SourceHealth.last_success_at`
证明有效；内容等级从 Task 2 的 `SourceMaterialPolicy` 读取，不依赖是否已有文章。

- [ ] **Step 5: 实现窗口内材料等级计数**

按 `DocumentVersion.fetched_at >= window_start` 且 `< window_end`，结合 `DocumentProvenance.content_scope` 分组计数。相同 `content_group_hash` 在一个平台内只计一次，避免 RSS、邮件和人工转发重复增加数量。

- [ ] **Step 6: 运行定向测试**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_intelligence_repository.py tests/unit/test_intelligence_reports.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```powershell
git add -- src/commerce_agent/intelligence/reports.py src/commerce_agent/intelligence/repository.py tests/integration/test_intelligence_repository.py tests/unit/test_intelligence_reports.py
git commit -m "feat: report strict platform coverage"
```

### Task 4: 将公开来源和 GDELT 发现层拆成十个平台

**Files:**
- Modify: `src/commerce_agent/sources/public_sources.yaml`
- Modify: `src/commerce_agent/media/catalog.py`
- Modify: `tests/unit/test_source_registry.py`
- Modify: `tests/unit/test_media_catalog.py`
- Modify: `tests/smoke/test_public_sources.py`
- Create: `docs/operations/ten-platform-source-acceptance.md`

**Interfaces:**
- Consumes: Task 1 的来源合同、现有 RSS/API/HTML 采集器。
- Produces: 每个平台至少两个已登记候选；十个独立 GDELT `metadata_only` 查询；五家中文媒体候选保持禁用直到获得许可。

- [ ] **Step 1: 写出平台候选和 GDELT 拆分的失败测试**

```python
def test_each_platform_has_two_registered_candidate_publishers() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    for platform in Platform:
        candidates = [s for s in registry.sources if platform in s.platforms]
        assert len({s.publisher_key for s in candidates if s.publisher_key}) >= 2

def test_gdelt_has_one_bounded_query_per_platform() -> None:
    registry = SourceRegistry.from_yaml(PUBLIC_SOURCES)
    gdelt = [s for s in registry.sources if s.adapter is SourceAdapter.GDELT]
    assert len(gdelt) == 10
    assert all(len(source.platforms) == 1 for source in gdelt)
    assert all(source.content_scope is ContentScope.METADATA_ONLY for source in gdelt)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_source_registry.py tests/unit/test_media_catalog.py -q`

Expected: FAIL，因为当前只有一个跨平台 GDELT 查询，且部分平台没有两个独立出版机构候选。

- [ ] **Step 3: 注册十个平台的官方公开候选**

以下端点全部先以审查结果决定状态，不因“官方”自动启用：

| Platform | Candidate ID | Entry URL |
| --- | --- | --- |
| Amazon | `amazon-about-small-business` | `https://www.aboutamazon.com/news/small-business` |
| TEMU | `temu-press-corner` | `https://www.temu.com/br-en/press.html` |
| SHEIN | `shein-group-newsroom` | `https://www.sheingroup.com/newsroom` |
| AliExpress | `alibaba-group-news` | `https://www.alibabagroup.com/en-US/news-and-resource` |
| Shopee | `sea-group-news` | `https://www.sea.com/news` |
| eBay | `ebay-press-room` | `https://www.ebayinc.com/stories/press-room/` |
| Coupang | `coupang-korean-newsroom` | `https://news.coupang.com/` |
| Ozon | `ozon-investor-news` | `https://ir.ozon.com/news-and-events/news/` |
| Joybuy | `jd-corporate-blog` | `https://jdcorporateblog.com/` |
| TikTok Shop | `tiktok-newsroom` | `https://newsroom.tiktok.com/en-us/` |

禁止的来源保持 `denied`，需要书面授权的来源保持 `authorization_required`，未完成许可核验的来源保持 `pending_review`。

- [ ] **Step 4: 注册五家中文行业媒体候选**

使用稳定 ID：

```yaml
media-cifnews-cross-border
media-ennews-cross-border
media-chwang-cross-border
media-dsb-cross-border
media-100ec-cross-border
```

状态固定采用设计规格中的审查结论，全部 `enabled: false`。每家媒体只有在最近 90 日样本明确出现某个平台，并取得自动保存全文许可后，才把该平台加入它的 `platforms` 覆盖计数。

- [ ] **Step 5: 拆分十个 GDELT 查询**

十个平台使用以下固定别名：

```text
amazon: ("Amazon marketplace" OR "Amazon seller")
temu: TEMU
shein: SHEIN
aliexpress: AliExpress
shopee: Shopee
ebay: eBay
coupang: Coupang
ozon: Ozon
joybuy: Joybuy
tiktok_shop: "TikTok Shop"
```

每个别名表达式后固定追加
`(policy OR compliance OR regulation OR recall OR lawsuit OR tariff OR seller)`。
每个来源固定 `mode=artlist`、`format=json`、`maxrecords=25`、`timespan=1d`、
`sort=datedesc`、`item_limit=25`，`enabled=false` 直到单次冒烟不返回 429。
GDELT 只保存标题、域名、发现时间和原文 URL，不计入 `2/2`。

- [ ] **Step 6: 为每个候选建立许可和冒烟记录**

`docs/operations/ten-platform-source-acceptance.md` 每条记录必须包含：

```markdown
## amazon-about-small-business
- Platform: Amazon
- Publisher: About Amazon
- Entry URL: https://www.aboutamazon.com/news/small-business
- Terms evidence: not granted
- Robots evidence: not reviewed
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed
- Offline fixture: not captured
- Live smoke date and result: not run
- Final status: pending_review / enabled=false
```

每个字段必须填入实际值或明确写 `not granted`；`not granted` 对应来源不得启用。

- [ ] **Step 7: 运行离线注册表和受控冒烟选择测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_source_registry.py tests/unit/test_media_catalog.py tests/smoke/test_public_sources.py -q`

Expected: PASS；网络烟测仍由 `RUN_PUBLIC_SOURCE_SMOKE=1` 显式开启，每次只验证一个来源、一个列表请求。

- [ ] **Step 8: 提交候选注册，不提交未经冒烟的启用变化**

```powershell
git add -- src/commerce_agent/sources/public_sources.yaml src/commerce_agent/media/catalog.py tests/unit/test_source_registry.py tests/unit/test_media_catalog.py tests/smoke/test_public_sources.py docs/operations/ten-platform-source-acceptance.md
git commit -m "feat: register ten-platform source candidates"
```

### Task 5: 实现飞书人工官方通知入口

**Files:**
- Create: `src/commerce_agent/ingestion/providers.py`
- Create: `src/commerce_agent/ingestion/official_notices.py`
- Create: `src/commerce_agent/ingestion/manual_submissions.py`
- Create: `src/commerce_agent/sources/official_accounts.yaml`
- Modify: `src/commerce_agent/domain.py`
- Modify: `src/commerce_agent/command_parser.py`
- Modify: `src/commerce_agent/application.py`
- Modify: `src/commerce_agent/integrations/feishu.py`
- Modify: `src/commerce_agent/runtime.py`
- Modify: `src/commerce_agent/persistence/models.py`
- Modify: `src/commerce_agent/persistence/ingestion.py`
- Create: `tests/unit/test_official_notices.py`
- Create: `tests/unit/test_manual_submissions.py`
- Modify: `tests/integration/test_ingestion_repository.py`
- Modify: `tests/unit/test_command_parser.py`
- Modify: `tests/unit/test_application.py`
- Modify: `tests/unit/test_feishu.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True, slots=True)
class OfficialNotice:
    platform: Platform
    source_account: str
    original_url: str
    title: str
    body: str
    published_at: datetime | None
    received_at: datetime
    submitted_by: str
    transport: Literal["feishu", "email", "api"]

class OfficialNoticeProvider(Protocol):
    async def poll(self) -> tuple[OfficialNotice, ...]: ...
```

人工提交审计使用只新增表：

```python
class OfficialNoticeAudit(Base):
    __tablename__ = "official_notice_audits"

    audit_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    transport: Mapped[str] = mapped_column(String(16), nullable=False)
    source_account: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_by_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
```

- [ ] **Step 1: 写出提交格式解析测试**

支持的飞书文本格式固定为：

```text
提交情报
平台: amazon
来源账号: 亚马逊全球开店
标题: 亚马逊更新某项卖家政策
原文: https://mp.weixin.qq.com/s/example
正文:
这里是团队成员从官方渠道取得并允许内部分析的完整正文。
```

测试：

```python
def test_parse_manual_submission() -> None:
    parsed = parse_manual_submission(SAMPLE)
    assert parsed.platform is Platform.AMAZON
    assert parsed.original_url == "https://mp.weixin.qq.com/s/example"
    assert parsed.body.startswith("这里是")
```

- [ ] **Step 2: 写出账号伪造和隐私数据拒绝测试**

```python
@pytest.mark.parametrize("url", [
    "https://example.com/not-wechat",
    "http://mp.weixin.qq.com/s/insecure",
])
def test_rejects_untrusted_official_link(url: str) -> None:
    with pytest.raises(NoticeValidationError, match="untrusted_original_url"):
        validate_notice(replace(_notice(), original_url=url), OFFICIAL_ACCOUNTS)

def test_rejects_account_level_private_data() -> None:
    notice = replace(_notice(), body="订单号 123456，买家邮箱 buyer@example.com，余额 10 元")
    with pytest.raises(NoticeValidationError, match="account_private_data"):
        validate_notice(notice, OFFICIAL_ACCOUNTS)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_official_notices.py tests/unit/test_manual_submissions.py tests/unit/test_command_parser.py -q`

Expected: FAIL，因为提交命令、账号白名单和通知对象尚不存在。

- [ ] **Step 4: 建立官方账号白名单**

YAML 只包含设计确认的十个平台和明确账号：

```yaml
version: 1
accounts:
  - account_id: amazon-global-selling-cn
    source_id: official-notice-amazon-global-selling-cn
    display_name: 亚马逊全球开店
    platforms: [amazon]
    allowed_hosts: [mp.weixin.qq.com]
    transports: [feishu, email]
```

`source_id` 是材料入库和覆盖统计使用的稳定身份，一个账号只能绑定一个出版机构；
尚未人工核实账号名称的平台不创建宽泛通配条目。账号名称必须精确匹配，不能使用包含匹配。

- [ ] **Step 5: 实现脱敏与拒绝边界**

在任何快照、日志或 LLM 调用前执行：

```python
SENSITIVE_PATTERNS = {
    "email": re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b"),
    "phone": re.compile(r"(?<!\d)(?:\+?\d[\d -]{8,}\d)(?!\d)"),
    "order": re.compile(r"(?:订单|order)\s*(?:号|id)?\s*[:：#]?\s*[A-Z0-9-]{6,}", re.I),
    "balance": re.compile(r"(?:余额|balance)\s*[:：]?\s*\d", re.I),
    "buyer": re.compile(r"(?:买家|buyer)\s*(?:id|账号|姓名|邮箱|phone)", re.I),
}
```

发现任何账号级数据时整条拒绝，不采用“删掉几段后继续分析”的宽松行为。

- [ ] **Step 6: 把命令接入已绑定群**

新增 `CommandKind.SUBMIT_INTELLIGENCE`。`BotService` 只有在
`bindings.is_active(chat_id)` 为真时调用 `ManualSubmissionService.submit(message)`，
并用实际审计编号生成成功回复：

```python
return (
    "✅ 已接收官方材料，等待正文校验和 AI 分析。"
    f"材料编号：{result.audit_id}"
)
```

日志只写 `audit_id`、平台、账号 ID、正文长度和固定错误码，不写正文或 URL 查询参数。

验证通过的正文按 Task 2 的 `PersistableDocument` 合同写入正常文档链路；
`submitted_by` 和原始 URL 只保存 SHA-256 审计哈希，不写日报。
`OfficialAccountRegistry` 在第一次接收前把账号对应的 `Source`、`SourcePlatform` 和
`SourceMaterialPolicy(full_text)` 幂等同步到数据库，`collector` 固定记录为 `manual_notice`，
不进入公开网页的周期采集列表。

- [ ] **Step 7: 运行定向测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_official_notices.py tests/unit/test_manual_submissions.py tests/unit/test_command_parser.py tests/unit/test_application.py tests/unit/test_feishu.py tests/unit/test_runtime.py tests/integration/test_ingestion_repository.py -q`

Expected: PASS。

- [ ] **Step 8: 提交**

```powershell
git add -- src/commerce_agent/ingestion/providers.py src/commerce_agent/ingestion/official_notices.py src/commerce_agent/ingestion/manual_submissions.py src/commerce_agent/sources/official_accounts.yaml src/commerce_agent/domain.py src/commerce_agent/command_parser.py src/commerce_agent/application.py src/commerce_agent/integrations/feishu.py src/commerce_agent/runtime.py src/commerce_agent/persistence/models.py src/commerce_agent/persistence/ingestion.py tests/unit/test_official_notices.py tests/unit/test_manual_submissions.py tests/unit/test_command_parser.py tests/unit/test_application.py tests/unit/test_feishu.py tests/integration/test_ingestion_repository.py
git commit -m "feat: accept audited official notices from Feishu"
```

### Task 6: 实现可选的专用邮箱官方通知入口

**Files:**
- Create: `src/commerce_agent/ingestion/email_notices.py`
- Modify: `src/commerce_agent/config.py`
- Modify: `.env.example`
- Modify: `src/commerce_agent/runtime.py`
- Create: `tests/unit/test_email_notices.py`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_runtime.py`

**Interfaces:**
- Consumes: Task 5 的 `OfficialNoticeProvider`、账号白名单和 `OfficialNotice`。
- Produces: `ImapOfficialNoticeProvider.poll() -> tuple[OfficialNotice, ...]`；默认关闭。

- [ ] **Step 1: 写出邮箱功能默认关闭和秘密字段测试**

```python
def test_email_notice_ingestion_defaults_off(settings_values) -> None:
    settings = Settings(**settings_values)
    assert settings.official_notice_email_enabled is False

def test_email_password_is_secret(settings_values) -> None:
    settings = Settings(
        **settings_values,
        official_notice_email_password="test-value",
    )
    assert "test-value" not in repr(settings)
```

- [ ] **Step 2: 写出邮件允许名单和附件上限测试**

```python
async def test_poll_accepts_only_allowlisted_sender(fake_imap) -> None:
    provider = ImapOfficialNoticeProvider(
        fake_imap,
        allowed_senders={"notice@amazon.com": "amazon-global-selling-cn"},
        max_message_bytes=1_000_000,
        max_attachment_bytes=2_000_000,
    )
    notices = await provider.poll()
    assert [notice.platform for notice in notices] == [Platform.AMAZON]

async def test_oversized_attachment_is_rejected(fake_imap) -> None:
    fake_imap.add_message(sender="notice@amazon.com", attachment=b"x" * 2_000_001)
    assert await provider.poll() == ()
    assert provider.last_error_code == "attachment_too_large"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_email_notices.py tests/unit/test_config.py -q`

Expected: FAIL，因为邮箱设置和适配器尚不存在。

- [ ] **Step 4: 添加严格配置**

```python
official_notice_email_enabled: bool = False
official_notice_email_host: str | None = None
official_notice_email_port: int = Field(default=993, ge=1, le=65535)
official_notice_email_username: str | None = None
official_notice_email_password: SecretStr | None = None
official_notice_email_folder: str = "INBOX"
official_notice_email_allowed_senders: str = ""
official_notice_email_max_message_bytes: int = Field(default=1_000_000, ge=1)
official_notice_email_max_attachment_bytes: int = Field(default=2_000_000, ge=1)
```

启用时要求 host、username、password 和非空发件人允许名单；密码只从 `.env` 读取，不写数据库。

- [ ] **Step 5: 用 `asyncio.to_thread` 包装标准库 IMAP**

连接使用 TLS、证书校验和固定 folder；每轮只拉取未处理 UID，成功入库后记录 UID 哈希。HTML 邮件先转纯文本，附件只允许 `.txt`、`.html`、`.pdf`，PDF 仅在已有安全文本提取器可用时接收；否则返回 `unsupported_attachment`。

- [ ] **Step 6: 将邮箱轮询接入采集调度**

邮箱 provider 作为独立来源运行，失败不影响公开来源。关闭开关时不创建连接、不读取任何邮箱设置。

- [ ] **Step 7: 运行定向测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_email_notices.py tests/unit/test_config.py tests/unit/test_runtime.py -q`

Expected: PASS。

- [ ] **Step 8: 提交**

```powershell
git add -- src/commerce_agent/ingestion/email_notices.py src/commerce_agent/config.py .env.example src/commerce_agent/runtime.py tests/unit/test_email_notices.py tests/unit/test_config.py tests/unit/test_runtime.py
git commit -m "feat: add optional official notice mailbox"
```

### Task 7: 跨入口去重并保持多平台归因

**Files:**
- Modify: `src/commerce_agent/ingestion/dedupe.py`
- Modify: `src/commerce_agent/persistence/models.py`
- Modify: `src/commerce_agent/persistence/ingestion.py`
- Modify: `src/commerce_agent/intelligence/repository.py`
- Modify: `src/commerce_agent/intelligence/reports.py`
- Modify: `tests/unit/test_deduplication.py`
- Modify: `tests/integration/test_ingestion_repository.py`
- Modify: `tests/integration/test_intelligence_repository.py`
- Modify: `tests/unit/test_intelligence_reports.py`

**Interfaces:**
- Consumes: 规范化 URL、`content_group_hash`、`event_fingerprint` 和来源平台。
- Produces: `AnalysisDuplicate` 关联表；同一正文跨 RSS、邮件、飞书提交只分析一次、
  日报只展示一次，但汇总所有明确平台和出处。

- [ ] **Step 1: 写出微信公众号追踪参数去除测试**

```python
def test_wechat_tracking_parameters_do_not_change_identity() -> None:
    first = canonicalize_url("https://mp.weixin.qq.com/s/abc?scene=1&from=timeline")
    second = canonicalize_url("https://mp.weixin.qq.com/s/abc")
    assert first == second
```

- [ ] **Step 2: 写出同正文跨来源只领取一次测试**

```python
async def test_same_body_from_email_and_feishu_is_analyzed_once(tmp_path) -> None:
    await _persist(source_id="amazon-email", body=BODY)
    await _persist(source_id="amazon-feishu", body=BODY)
    first = await repository.claim_next(now=NOW)
    assert first is not None
    await repository.complete_analysis(
        first,
        _valid_result(),
        90,
        "event-1",
        risk_level=RiskLevel.HIGH,
        now=NOW,
        model_name="test-model",
    )
    second = await repository.claim_next(now=NOW)
    assert second is None
```

- [ ] **Step 3: 写出一条事件可显示多个平台测试**

```python
def test_report_merges_platforms_for_same_event() -> None:
    draft = composer.compose(
        report_date=REPORT_DATE,
        analyses=(
            _analysis(fingerprint="event-1", platforms=(Platform.AMAZON,)),
            _analysis(fingerprint="event-1", platforms=(Platform.EBAY,)),
        ),
    )
    assert draft.payload["items"][0]["platforms"] == ["amazon", "ebay"]
```

- [ ] **Step 4: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_deduplication.py tests/integration/test_intelligence_repository.py tests/unit/test_intelligence_reports.py -q`

Expected: FAIL，因为跨来源相同正文仍会创建并领取两个分析作业，代表项会丢失另一来源的平台。

- [ ] **Step 5: 用 `content_group_hash` 复用已完成分析**

新增只增表：

```python
class AnalysisDuplicate(Base):
    __tablename__ = "analysis_duplicates"

    duplicate_document_version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    canonical_analysis_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("document_analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
```

领取作业前，若同一 `content_group_hash` 已有完成分析，则把当前作业标记为
`duplicate` 并写入 `AnalysisDuplicate`；不得复制模型原始输出到日志。
若同批次有两个未分析重复项，只允许最早的 `AnalysisJob.id` 被领取。

- [ ] **Step 6: 在日报聚合器合并平台和出处**

`list_report_analyses()` 同时读取完成作业和 `AnalysisDuplicate` 关联的平台与出处。
同一 `event_fingerprint` 选择最高质量代表项，同时计算：

```python
platforms = sorted({
    platform.value
    for item in event_items
    for platform in item.candidate.platforms
})
source_references = sorted({
    (item.candidate.source_name, item.candidate.canonical_url)
    for item in event_items
})
```

LLM 仍不得添加来源文本没有明确支持的平台。

- [ ] **Step 7: 运行定向测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_deduplication.py tests/integration/test_ingestion_repository.py tests/integration/test_intelligence_repository.py tests/unit/test_intelligence_reports.py -q`

Expected: PASS。

- [ ] **Step 8: 提交**

```powershell
git add -- src/commerce_agent/ingestion/dedupe.py src/commerce_agent/persistence/models.py src/commerce_agent/persistence/ingestion.py src/commerce_agent/intelligence/repository.py src/commerce_agent/intelligence/reports.py tests/unit/test_deduplication.py tests/integration/test_ingestion_repository.py tests/integration/test_intelligence_repository.py tests/unit/test_intelligence_reports.py
git commit -m "feat: deduplicate notices across ingestion channels"
```

### Task 8: 编排 08:40 预采集、08:55 截止和 09:00 幂等发送

**Files:**
- Create: `src/commerce_agent/ingestion/pre_report.py`
- Modify: `src/commerce_agent/ingestion/scheduler.py`
- Modify: `src/commerce_agent/intelligence/scheduler.py`
- Modify: `src/commerce_agent/intelligence/reports.py`
- Modify: `src/commerce_agent/runtime.py`
- Create: `tests/unit/test_pre_report_pipeline.py`
- Modify: `tests/unit/test_ingestion_scheduler.py`
- Modify: `tests/unit/test_intelligence_scheduler.py`
- Modify: `tests/unit/test_runtime.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True, slots=True)
class PreReportResult:
    report_date: date
    source_timeouts: tuple[str, ...]
    analysis_claimed: int
    report_prepared: bool

class PreReportPipeline:
    async def prepare(self, group_id: str, report_date: date) -> PreReportResult: ...
```

- [ ] **Step 1: 写出 08:40 和 09:00 两个作业测试**

```python
def test_scheduler_registers_prepare_and_send_jobs() -> None:
    scheduler.start()
    prepare = fake_scheduler.get_job("intelligence-daily-prepare")
    send = fake_scheduler.get_job("intelligence-daily-report")
    assert prepare.trigger.fields[5].expressions[0].first == 8
    assert prepare.trigger.fields[6].expressions[0].first == 40
    assert send.trigger.fields[5].expressions[0].first == 9
    assert send.trigger.fields[6].expressions[0].first == 0
```

- [ ] **Step 2: 写出慢来源在截止后被取消但日报仍预览的测试**

```python
async def test_prepare_cancels_slow_sources_and_saves_preview(fake_clock) -> None:
    result = await pipeline.prepare("chat-one", date(2026, 7, 28))
    assert result.source_timeouts == ("slow-source",)
    assert reports.preview_calls == [("chat-one", date(2026, 7, 28))]
```

- [ ] **Step 3: 写出 09:00 只排队已有预览的测试**

```python
async def test_send_job_queues_previewed_report_once() -> None:
    await scheduler._run_daily_send()
    await scheduler._run_daily_send()
    assert reports.queue_previewed_calls == 2
    assert repository.outbox_count == 1
```

- [ ] **Step 4: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_pre_report_pipeline.py tests/unit/test_intelligence_scheduler.py tests/unit/test_runtime.py -q`

Expected: FAIL，因为当前只有 09:00 的单个 `generate_and_queue` 作业。

- [ ] **Step 5: 实现有界预处理**

`prepare()` 使用上海时区计算两个绝对截止时间：

```python
collect_deadline = datetime.combine(report_date, time(8, 55), tzinfo=timezone)
preview_deadline = datetime.combine(report_date, time(8, 59), tzinfo=timezone)
```

08:40 启动 `run_all(Trigger.SCHEDULED)`；到 08:55 取消未完成来源并记录 `source_id`。随后循环 `analysis.drain(limit=20)`，直到 `claimed=0` 或到达 08:59，最后调用 `reports.preview(group_id, report_date)`。

- [ ] **Step 6: 分离准备和发送作业**

新增常量：

```python
DAILY_PREPARE_JOB_ID = "intelligence-daily-prepare"
DAILY_JOB_ID = "intelligence-daily-report"
```

09:00 只调用 `queue_previewed()`；若本机在 08:40–08:59 未运行导致没有预览，记录 `daily_report_not_prepared`，不临时无限等待来源。管理员可用既有补发命令生成当日更正版本。

- [ ] **Step 7: 运行定向测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_pre_report_pipeline.py tests/unit/test_ingestion_scheduler.py tests/unit/test_intelligence_scheduler.py tests/unit/test_runtime.py tests/integration/test_intelligence_outbox.py -q`

Expected: PASS。

- [ ] **Step 8: 提交**

```powershell
git add -- src/commerce_agent/ingestion/pre_report.py src/commerce_agent/ingestion/scheduler.py src/commerce_agent/intelligence/scheduler.py src/commerce_agent/intelligence/reports.py src/commerce_agent/runtime.py tests/unit/test_pre_report_pipeline.py tests/unit/test_ingestion_scheduler.py tests/unit/test_intelligence_scheduler.py tests/unit/test_runtime.py
git commit -m "feat: prepare daily intelligence before nine"
```

### Task 9: 在飞书日报展示覆盖、线索和来源异常

**Files:**
- Modify: `src/commerce_agent/intelligence/reports.py`
- Modify: `src/commerce_agent/intelligence/delivery.py`
- Modify: `tests/unit/test_intelligence_reports.py`
- Modify: `tests/unit/test_intelligence_delivery.py`
- Modify: `tests/integration/test_intelligence_pipeline.py`

**Interfaces:**
- Consumes: Task 3 的 `CoverageRow`、现有风险和证据 payload。
- Produces: “今日覆盖”和“来源异常”两个区块；`feed_summary` 只显示为待核实线索，不生成风险等级、置信度或建议动作。

- [ ] **Step 1: 写出覆盖摘要测试**

```python
def test_daily_card_shows_platform_and_source_coverage() -> None:
    payload = build_health_payload(REPORT_DATE, COVERAGE, RiskProfile.DEFAULT)
    coverage = next(section for section in payload["sections"] if section["title"] == "今日覆盖")
    assert "平台 8/10｜有效来源 17/20" in coverage["items"]
    assert "Amazon 2/2｜正文 3｜摘要线索 1｜元数据线索 0" in coverage["items"]
```

- [ ] **Step 2: 写出摘要线索不含风险判断的测试**

```python
def test_feed_summary_lead_has_no_risk_or_action() -> None:
    lead = _find_summary_lead(payload)
    assert lead["label"] == "仅摘要，待核实"
    assert "risk_level" not in lead
    assert "evidence_confidence" not in lead
    assert "actions" not in lead
```

- [ ] **Step 3: 写出异常信息用户可读且不泄密的测试**

```python
def test_source_anomaly_is_safe_and_readable() -> None:
    text = semantic_to_text(payload)
    assert "Ozon 第二来源今日超时，本次为部分覆盖" in text
    assert "Traceback" not in text
    assert "cookie" not in text.lower()
    assert "chat_id" not in text
```

- [ ] **Step 4: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_intelligence_reports.py tests/unit/test_intelligence_delivery.py -q`

Expected: FAIL，因为当前覆盖区只有“启用来源/已验证更新”的粗粒度文本。

- [ ] **Step 5: 生成固定顺序的覆盖文本**

平台按 `Platform` 枚举顺序输出；总览计算：

```python
covered_platforms = sum(row.effective_source_count > 0 for row in coverage)
effective_sources = sum(min(row.effective_source_count, 2) for row in coverage)
headline = f"平台 {covered_platforms}/10｜有效来源 {effective_sources}/20"
```

每个平台输出 `x/2`、正文数、摘要线索数和元数据线索数。没有更新时写“无已验证更新”，不得复用旧闻填充。

- [ ] **Step 6: 生成受控异常文案**

只允许以下异常类别映射到卡片：

```python
ANOMALY_LABELS = {
    "timeout": "今日超时，本次为部分覆盖",
    "suspended": "连续失败，已暂停并等待复核",
    "summary_only": "仅返回摘要，未进入 AI 结论",
    "no_full_text": "暂无合规完整正文来源",
    "authorization_required": "需要来源授权，当前未启用",
}
```

未映射错误只显示“来源暂不可用”，不显示堆栈、URL 参数、响应正文或内部 ID。

- [ ] **Step 7: 运行定向和集成测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_intelligence_reports.py tests/unit/test_intelligence_delivery.py tests/integration/test_intelligence_pipeline.py -q`

Expected: PASS。

- [ ] **Step 8: 提交**

```powershell
git add -- src/commerce_agent/intelligence/reports.py src/commerce_agent/intelligence/delivery.py tests/unit/test_intelligence_reports.py tests/unit/test_intelligence_delivery.py tests/integration/test_intelligence_pipeline.py
git commit -m "feat: show ten-platform coverage in daily cards"
```

### Task 10: 预留付费全文接口并完成十平台验收

**Files:**
- Modify: `src/commerce_agent/ingestion/providers.py`
- Create: `tests/contract/test_licensed_news_provider.py`
- Modify: `docs/operations/ten-platform-source-acceptance.md`
- Modify: `docs/operations/source-ingestion-runbook.md`
- Modify: `README.md`

**Interfaces:**
- Produces:

```python
class LicensedNewsProvider(Protocol):
    async def fetch(
        self,
        *,
        platforms: tuple[Platform, ...],
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[LicensedArticle, ...]: ...
```

实现为空适配器 `DisabledLicensedNewsProvider`，返回空元组，不访问网络、不要求密钥。

```python
@dataclass(frozen=True, slots=True)
class LicensedArticle:
    platform: Platform
    publisher_key: str
    attribution: str
    original_url: str
    title: str
    body: str
    published_at: datetime | None
    received_at: datetime
```

- [ ] **Step 1: 写出供应商无关契约测试**

```python
async def assert_provider_contract(provider: LicensedNewsProvider) -> None:
    items = await provider.fetch(
        platforms=(Platform.AMAZON,),
        window_start=START,
        window_end=END,
    )
    for item in items:
        assert item.platform is Platform.AMAZON
        assert item.original_url.startswith("https://")
        assert item.body.strip()
```

- [ ] **Step 2: 写出禁用适配器零网络测试**

```python
async def test_disabled_provider_is_inert() -> None:
    provider = DisabledLicensedNewsProvider()
    assert await provider.fetch(
        platforms=tuple(Platform),
        window_start=START,
        window_end=END,
    ) == ()
```

- [ ] **Step 3: 实现协议和空适配器**

协议返回稳定的 `LicensedArticle`，由持久化适配器转换为与其他来源一致的
`PersistableDocument`，使日报、去重和分析层不依赖某家供应商。
供应商凭据必须通过 `SecretStr` 设置注入，禁止写入 YAML。

- [ ] **Step 4: 运行全量离线验证**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: 全部通过；公开网络烟测仅有显式跳过。

Run: `.venv\Scripts\python.exe -m ruff check .`

Expected: `All checks passed!`

Run: `git diff --check`

Expected: 无输出。

- [ ] **Step 5: 执行每个候选的单来源受控冒烟**

网络验收按固定来源 ID 逐个运行：

```powershell
$env:RUN_PUBLIC_SOURCE_SMOKE='1'
.venv\Scripts\python.exe -m pytest tests/smoke/test_public_sources.py -q
$sourceIds = @(
  'amazon-about-small-business',
  'temu-press-corner',
  'shein-group-newsroom',
  'alibaba-group-news',
  'sea-group-news',
  'ebay-press-room',
  'coupang-korean-newsroom',
  'ozon-investor-news',
  'jd-corporate-blog',
  'tiktok-newsroom'
)
foreach ($sourceId in $sourceIds) {
  .venv\Scripts\python.exe -m commerce_agent.ingestion_cli run --source $sourceId
}
```

只有实际通过的来源才在同一审查提交中改成 `allowed + enabled=true`；401、403、验证码、登录跳转、条款禁止、429 或正文缺失均保持关闭。

- [ ] **Step 6: 执行三轮计划采集健康门**

对所有计入 `2/2` 的来源连续运行三轮正常调度，确认：

```text
consecutive_failures = 0
health_status = healthy
content_scope = full_text
publisher_key 非空
最近 90 日存在对应平台材料或本轮真实冒烟命中
```

- [ ] **Step 7: 执行不发送的 08:40–09:00 演练**

使用假时钟或 CLI dry-run，确认 08:55 慢来源被截断、08:59 已有预览、09:00 只生成一个 outbox 项，且卡片不包含正文、秘密、群 ID 或内部错误。

- [ ] **Step 8: 检查严格上线门**

生成平台矩阵：

```text
Amazon      x/2
TEMU        x/2
SHEIN       x/2
AliExpress  x/2
Shopee      x/2
eBay        x/2
Coupang     x/2
Ozon        x/2
Joybuy      x/2
TikTok Shop x/2
```

任何平台小于 `2/2` 时，机器人可以继续以“部分覆盖”运行和测试，但不得把“十平台完整覆盖”标记为上线完成。由用户决定是保持免费部分覆盖，还是为缺口取得书面授权/付费全文 API。

- [ ] **Step 9: 提交协议和验收文档**

```powershell
git add -- src/commerce_agent/ingestion/providers.py tests/contract/test_licensed_news_provider.py docs/operations/ten-platform-source-acceptance.md docs/operations/source-ingestion-runbook.md README.md
git commit -m "docs: define ten-platform coverage acceptance"
```

## Final Verification

- [ ] `.venv\Scripts\python.exe -m pytest -q` 全部通过。
- [ ] `.venv\Scripts\python.exe -m ruff check .` 通过。
- [ ] `git diff --check` 通过。
- [ ] `git status --short` 只包含本计划内文件。
- [ ] 暂存差异不包含 `.env`、API key、App Secret、邮箱密码、Cookie、Token、群 ID 或用户正文。
- [ ] 注册表只包含原有十个平台。
- [ ] `feed_summary` 和 `metadata_only` 的模拟 LLM 调用次数为 0。
- [ ] 同一正文从 RSS、邮箱和飞书到达时只形成一个日报事件。
- [ ] 08:40、08:55、08:59、09:00 的假时钟测试全部通过。
- [ ] 日报显示风险等级、置信度、判断依据、建议动作和原文链接，但不复制受限全文。
- [ ] 未达到 `2/2` 的平台明确显示覆盖缺口，不使用旧闻或未经授权内容补数。

## Rollback

- 新来源异常：只把对应 `source_id` 改为 `enabled=false`，保留审计记录和已入库材料。
- 邮箱异常：设置 `OFFICIAL_NOTICE_EMAIL_ENABLED=false` 并重启，不删除已处理 UID 审计。
- 人工提交异常：从 `BotService` 关闭提交服务注入，其他命令和日报继续运行。
- 预处理异常：关闭新 08:40 作业，恢复现有 09:00 `generate_and_queue` 行为；Outbox 唯一键继续防止重复。
- 覆盖查询异常：日报回退为现有“无已验证更新”健康卡片，不阻塞 Outbox 投递。
- 数据库变更只新增表或幂等记录；不删除用户现有 SQLite 数据，不执行破坏性迁移。
