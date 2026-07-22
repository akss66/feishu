# 六项整改第一版实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不降低合规与安全边界的前提下，让本机机器人稳定自启动、可恢复探测媒体源、展示完整依据并降低 AI 分析失败。

**Architecture:** 以现有模块化单体为基础，Windows 脚本只提供非敏感运行覆盖；采集服务增加受合规策略约束的显式 probe；媒体全文继续由不可变出版机构目录控制；展示和 AI 稳定性保持为独立纯逻辑切片。

**Tech Stack:** Python 3.11、Pydantic Settings 2.x、SQLAlchemy 2.x、APScheduler 3.x、PowerShell 5.1 ScheduledTasks、pytest、Ruff。

## Global Constraints

- 不读取、打印、改写或提交 `.env`。
- 不新增第三方依赖。
- 不启用 `denied`、`authorization_required` 或 `pending_review` 来源。
- 不绕过 HTTP 429、登录、验证码、付费墙、robots、DNS/SSRF 检查。
- 每个行为变更必须先运行失败测试，再实现最小修复。

---

### Task 1: 稳定本机启动

**Files:**
- Create: `scripts/run-commerce-agent.ps1`
- Create: `scripts/install-commerce-agent-autostart.ps1`
- Create: `tests/operations/test_powershell_scripts.py`
- Modify: `docs/operations/intelligence-delivery-runbook.md`

**Interfaces:**
- Consumes: 现有 `python -m commerce_agent` 和 `.env` 加载行为。
- Produces: 前台启动脚本，以及当前用户登录触发的 `CrossBorderCommerceAgent` 计划任务。

- [ ] 写脚本语法与安全断言测试，验证无 `.env` 读写命令、七个非敏感环境覆盖、60 秒超时和计划任务安全设置。
- [ ] 运行 `python -m pytest tests/operations/test_powershell_scripts.py -q`，确认因脚本不存在而失败。
- [ ] 用启动脚本设置运行开关并追加写入按日期日志；用安装脚本创建当前用户登录触发、`IgnoreNew`、失败重启的计划任务，拒绝覆盖未知同名任务。
- [ ] 重新运行脚本测试并用 PowerShell Parser 解析两个脚本，预期全部通过。
- [ ] 提交 `feat: add stable local bot startup`。

### Task 2: 受控探测与明确限流

**Files:**
- Modify: `src/commerce_agent/ingestion/compliance.py`
- Modify: `src/commerce_agent/ingestion/service.py`
- Modify: `src/commerce_agent/ingestion/http.py`
- Modify: `src/commerce_agent/ingestion_cli.py`
- Modify: `tests/unit/test_ingestion_service.py`
- Modify: `tests/unit/test_ingestion_http.py`
- Modify: `tests/unit/test_ingestion_cli.py`

**Interfaces:**
- Consumes: `SourceDefinition.compliance`, `SourceDefinition.enabled` 和现有网络安全策略。
- Produces: `CompliancePolicy.require_probeable(source)`、`IngestionService.probe_source(source_id)`、`probe --source SOURCE_ID` 与 `rate_limited`。

- [ ] 写失败测试：禁用但 allowed 可 probe，授权/禁止不可 probe，普通 run 仍拒绝禁用来源，HTTP 429 为 `rate_limited`，CLI 输出受控结果。
- [ ] 运行三个目标测试文件，确认新测试失败。
- [ ] 实现独立 probe 合规入口并复用同一采集执行路径；实现 429 专用安全错误码。
- [ ] 运行三个目标测试文件，预期全部通过。
- [ ] 提交 `feat: add governed source probes`。

### Task 3: 获准媒体正文持久化

**Files:**
- Modify: `src/commerce_agent/persistence/ingestion.py`
- Modify: `tests/integration/test_ingestion_repository.py`
- Modify: `tests/integration/test_ingestion_pipeline.py`
- Modify: `docs/operations/media-source-compliance-review-2026-07-22.md`

**Interfaces:**
- Consumes: GDELT 采集器仅为 `ArticleAccess.ALLOWED_PUBLIC` 产生的 `content_scope=full_text`。
- Produces: 完整 provenance 下可持久化的媒体全文；其他媒体策略不变。

- [ ] 写失败测试：完整媒体 provenance 可保存 `full_text`，不完整 provenance 和未知 scope 仍拒绝；允许出版机构的 fixture 全链路进入待分析队列。
- [ ] 运行两个目标集成测试文件，确认 full_text 被现有持久层拒绝。
- [ ] 将持久层允许范围扩为 `metadata_only`、`feed_summary`、`full_text`，不改变注册表对静态 full_text 媒体源的禁用规则。
- [ ] 重新运行目标集成测试，预期全部通过。
- [ ] 提交 `fix: persist approved media full text`。

### Task 4: 十个平台覆盖矩阵

**Files:**
- Modify: `src/commerce_agent/ingestion_cli.py`
- Modify: `tests/unit/test_ingestion_cli.py`
- Modify: `docs/operations/source-compliance-review-2026-07-22.md`

**Interfaces:**
- Consumes: `SourceRegistry.sources` 与十个 `Platform` 枚举值。
- Produces: `sources coverage` 命令，每个平台一行合规计数与覆盖状态。

- [ ] 写失败测试，断言十个平台恰好各一行且 allowed/enabled/authorization/pending/denied/total 计数准确。
- [ ] 运行 `python -m pytest tests/unit/test_ingestion_cli.py -q`，确认命令尚不存在。
- [ ] 实现稳定排序的覆盖聚合与表格输出，不改变任何来源启用状态。
- [ ] 重新运行 CLI 测试，预期通过。
- [ ] 提交 `feat: expose platform source coverage`。

### Task 5: 飞书卡片显示媒体依据

**Files:**
- Modify: `src/commerce_agent/intelligence/delivery.py`
- Modify: `tests/unit/test_intelligence_delivery.py`

**Interfaces:**
- Consumes: 报告条目的可选 `media_category` 和 `content_basis`。
- Produces: 中文“来源类型”和“分析依据”行，卡片与文本降级共用 `alert_markdown`。

- [ ] 写失败测试，覆盖三类媒体、三种内容依据、缺失字段不展示和恶意值转义。
- [ ] 运行目标单元测试，确认标签未展示。
- [ ] 增加封闭中文映射和可选展示行。
- [ ] 重新运行目标单元测试，预期通过。
- [ ] 提交 `feat: explain media basis in Feishu cards`。

### Task 6: AI 输入与格式稳定性

**Files:**
- Modify: `src/commerce_agent/intelligence/analyzer.py`
- Modify: `tests/unit/test_intelligence_analyzer.py`
- Modify: `docs/operations/intelligence-delivery-runbook.md`

**Interfaces:**
- Produces: `analysis_excerpt(body, limit=50000)` 和 `MAX_ANALYSIS_ATTEMPTS=3`。

- [ ] 将现有超长拒绝测试改成失败的摘取行为测试，并新增首尾保留、硬上限、证据只锚定摘录和三次修复上限测试。
- [ ] 运行 `python -m pytest tests/unit/test_intelligence_analyzer.py -q`，确认新期望失败。
- [ ] 实现固定首尾摘取；用摘取后的候选构建载荷并做证据/事实校验；将模型尝试上限改为三次。
- [ ] 重新运行目标测试，预期通过。
- [ ] 提交 `fix: reduce avoidable analysis failures`。

### Task 7: 全量验收与本机切换

**Files:**
- Modify: `docs/operations/intelligence-delivery-runbook.md`

**Interfaces:**
- Consumes: 前六个切片。
- Produces: 已注册并启动的本机计划任务、单实例机器人和可审计验收记录。

- [ ] 运行 `python -m pytest`、`python -m ruff check .` 和 `git diff --check`，预期除既有说明跳过项外无失败。
- [ ] 查询同名计划任务；不存在时安装，存在且属于本项目时更新，存在但不属于本项目时停止并报告而不覆盖。
- [ ] 停止当前手工机器人进程，启动计划任务并验证只有一个 `python -m commerce_agent` 进程。
- [ ] 检查新日志确认 ingestion 和 intelligence 两个调度器启动；运行 `sources coverage` 与 GDELT probe，记录成功或明确的 `rate_limited` 外部限制。
- [ ] 生成一份日报预览并仅在存在明确待发送 outbox 时按用户已授权的测试群发送一次。
- [ ] 提交 `docs: record first-pass remediation acceptance`。
