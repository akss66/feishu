# Implementation Plan: 合规真实来源、媒体扩展与首份飞书日报

## Overview

本计划把已确认的两份设计落地为一条本机可运行、可审计的真实链路：使用安全的 Cloudflare DoH 获取 Amazon、eBay 和 GDELT 公开信息，保存媒体出处，按出版机构而不是采集渠道计算交叉验证，交给 AI 提炼，并在人工预览后向已绑定飞书测试群发送日报。同时预留配置式 RSS/API 和白名单专用适配器，使以后增加媒体不需要修改日报或调度核心。

## Baseline

- 分支：`codex/compliant-live-sources`
- Python：3.11.9
- 基线命令：`python -m pytest -q`
- 基线结果：全部现有测试通过，1 个显式跳过；只有第三方 `pkg_resources` 弃用警告。
- 设计依据：
  - `docs/superpowers/specs/2026-07-22-cloudflare-doh-resolver-design.md`
  - `docs/superpowers/specs/2026-07-22-compliant-live-sources-design.md`

## Architecture Decisions

- `collector` 表示 RSS/API 等传输类型，`adapter` 表示 `generic` 或代码白名单中的特殊解析器；YAML 不能动态导入 Python 模块。
- 媒体材料持久化到新建的 `document_provenance` 表，避免对用户现有 SQLite 表执行破坏性列迁移。
- `publisher_key` 表示真实出版机构。GDELT 和直接 RSS 返回的同一出版机构只算一个证据源。
- 第一版仅允许 `metadata_only` 和 `feed_summary`；`full_text` 即使出现在配置中也不能启用。
- 单一媒体出版机构的证据分数最高 70；两个不同出版机构或官方来源按现有规则计分。
- 实施命令不直接查看、打印、改写或提交 `.env`；应用仍按现有机制在进程内部加载凭据。所有新增验收开关仅设置在启动命令的当前进程。
- 真实端点在上线前保持关闭；只有现场冒烟通过的来源才最终改为 `enabled: true`。

## Out of Scope

- CPSC、EU Safety Gate 等中性监管源，直到模型支持不预先绑定平台的事件。
- TEMU、SHEIN、TikTok Shop、Coupang 等需要账号授权的卖家或合作伙伴 API。
- 媒体全文抓取、通用网页爬虫、登录态、Cookie、验证码和浏览器自动化。
- 云服务器迁移、即时预警调度和自动问答调度。

## Dependency Graph

```text
Task 1 DoH 核心
  -> Task 2 解析器工厂/CLI
  -> Task 3 运行时生命周期

Task 4 媒体来源合同
  -> Task 5 现有媒体候选注解
  -> Task 6 GDELT/发现链接解析
  -> Task 7 出处提取
  -> Task 8 出处持久化
  -> Task 9 出版机构交叉验证
  -> Task 10 单媒体置信度上限
  -> Task 11 真实来源注册
  -> Task 12 日报详情

Task 3 + Task 11 + Task 12
  -> Task 13 真实验收、飞书投递和本机定时运行
```

## Task 1: 实现安全的 Cloudflare DoH 解析器

**Description:** 新增固定 Cloudflare JSON DoH 解析器和严格配置项。解析器只返回合法 A/AAAA 地址，公网判断仍由现有 `UrlSafetyPolicy` 负责；任何上游异常都关闭失败。

**Acceptance criteria:**

- [ ] `INGESTION_DNS_MODE` 仅接受 `system` 和 `cloudflare_doh`，默认 `system`。
- [ ] DoH 客户端固定请求 `https://1.1.1.1/dns-query`，5 秒超时、64 KiB 上限、禁止重定向、`trust_env=False`。
- [ ] A/AAAA 去重保序；CNAME 不作为连接地址；错误状态、截断、畸形 JSON、空结果和非法 IP 统一失败且不泄漏响应正文。
- [ ] `aclose()` 幂等并释放客户端。

**Verification:**

- [ ] `python -m pytest tests/unit/test_ingestion_dns.py tests/unit/test_config.py -q`
- [ ] `python -m ruff check src/commerce_agent/ingestion/dns.py src/commerce_agent/config.py tests/unit/test_ingestion_dns.py tests/unit/test_config.py`

**Dependencies:** None

**Files likely touched:**

- Create: `src/commerce_agent/ingestion/dns.py`
- Modify: `src/commerce_agent/config.py`
- Modify: `.env.example`
- Create: `tests/unit/test_ingestion_dns.py`
- Modify: `tests/unit/test_config.py`

**Estimated scope:** Medium, 5 files

**Commit:** `feat: add fail-closed Cloudflare DoH resolver`

## Task 2: 建立共享解析器工厂并接入采集 CLI

**Description:** 建立一个小型解析器工厂并先接入管理员采集 CLI，使 `system` 与 `cloudflare_doh` 的选择逻辑只有一份。

**Acceptance criteria:**

- [ ] `system` 模式保持当前行为；`cloudflare_doh` 模式把同一个解析器实例注入 `UrlSafetyPolicy`。
- [ ] `ingestion_cli` 的精简设置模型也支持 `INGESTION_DNS_MODE`。
- [ ] CLI 正常退出或构建失败时按逆序关闭 HTTP 与 DoH 资源。
- [ ] 浏览器采集仍强制关闭，DoH 不改变任何 SSRF、端口、重定向或 host allowlist 规则。

**Verification:**

- [ ] `python -m pytest tests/unit/test_ingestion_bootstrap.py tests/unit/test_ingestion_cli.py -q`
- [ ] `python -m ruff check src/commerce_agent/ingestion/bootstrap.py src/commerce_agent/ingestion_cli.py`

**Dependencies:** Task 1

**Files likely touched:**

- Create: `src/commerce_agent/ingestion/bootstrap.py`
- Modify: `src/commerce_agent/ingestion_cli.py`
- Create: `tests/unit/test_ingestion_bootstrap.py`
- Modify: `tests/unit/test_ingestion_cli.py`

**Estimated scope:** Medium, 4 files

**Commit:** `feat: share ingestion resolver construction`

## Task 3: 接入机器人运行时并验证资源生命周期

**Description:** 让定时机器人使用 Task 2 的共享工厂，并在正常退出和部分构建失败时关闭 DoH 与 HTTP 资源。

**Acceptance criteria:**

- [ ] 定时运行时与 CLI 使用相同的解析器工厂。
- [ ] 运行时在 HTTP 客户端构建失败、调度器构建失败和正常退出时都释放已创建资源。
- [ ] `_close_resources` 继续按安全顺序关闭调度器、连接器、采集资源、AI 客户端和数据库。

**Verification:**

- [ ] `python -m pytest tests/unit/test_runtime.py -q`
- [ ] `python -m ruff check src/commerce_agent/runtime.py tests/unit/test_runtime.py`

**Dependencies:** Task 2

**Files likely touched:**

- Modify: `src/commerce_agent/runtime.py`
- Modify: `tests/unit/test_runtime.py`

**Estimated scope:** Small, 2 files

**Commit:** `feat: wire resolver lifecycle into bot runtime`

## Checkpoint A: 网络安全基础

- [ ] Task 1–3 的定向测试全部通过。
- [ ] `python -m pytest tests/unit/test_ingestion_http.py tests/unit/test_ingestion_security.py -q` 继续通过。
- [ ] 在未设置环境变量时，所有测试仍证明系统 DNS 是默认值。

## Task 4: 定义并验证可扩展媒体来源合同

**Description:** 扩展 `SourceDefinition` 和 YAML 注册表，明确适配器、内容范围、署名和出版机构标识。现有关闭状态的媒体候选也补齐合同，防止以后绕过审查直接启用。

**Acceptance criteria:**

- [ ] 新增受控枚举 `ContentScope(metadata_only, feed_summary, full_text)`，适配器只允许 `generic`、`gdelt`。
- [ ] 启用的 `media` 来源必须有 `content_scope` 和 `attribution`；直接来源必须有 `publisher_key`。
- [ ] `gdelt` 聚合器允许来源级 `publisher_key` 为空，但要求 API 配置包含逐条出版机构字段。
- [ ] 第一版拒绝启用 `full_text`；未知适配器和未知字段均拒绝。
- [ ] 现有官方来源和尚未注解的关闭媒体候选保持可加载；任何启用媒体必须满足完整合同。

**Verification:**

- [ ] `python -m pytest tests/unit/test_source_registry.py -q`
- [ ] `python -m ruff check src/commerce_agent/ingestion/models.py src/commerce_agent/ingestion/registry.py tests/unit/test_source_registry.py`

**Dependencies:** None

**Files likely touched:**

- Modify: `src/commerce_agent/ingestion/models.py`
- Modify: `src/commerce_agent/ingestion/registry.py`
- Modify: `tests/fixtures/ingestion/valid_sources.yaml`
- Modify: `tests/fixtures/ingestion/invalid_sources.yaml`
- Modify: `tests/unit/test_source_registry.py`

**Estimated scope:** Medium, 5 files

**Commit:** `feat: define media source provenance contract`

## Task 5: 为现有媒体候选补齐来源注解

**Description:** 给 Marketplace Pulse、EcommerceBytes、Digital Commerce 360 和 Reuters 等现有关闭候选补齐适配器、内容范围、署名和出版机构标识，不改变它们的合规或启用状态。

**Acceptance criteria:**

- [ ] 每个现有 `media` 条目都满足 Task 4 的完整合同。
- [ ] `denied` 与 `authorization_required` 条目仍为 `enabled: false`。
- [ ] CLI 可以列出全部来源，注册表中不存在未注解媒体。

**Verification:**

- [ ] `python -m pytest tests/unit/test_source_registry.py -q`
- [ ] `python -m commerce_agent.ingestion_cli sources list`

**Dependencies:** Task 4

**Files likely touched:**

- Modify: `src/commerce_agent/sources/public_sources.yaml`
- Modify: `tests/unit/test_source_registry.py`

**Estimated scope:** Small, 2 files

**Commit:** `chore: annotate reviewed media source candidates`

## Task 6: 解析 GDELT 元数据并加固发现链接

**Description:** 扩展 API 采集器，使白名单 `gdelt` 适配器从 `articles` 中读取 URL、标题、`seendate` 和 `domain`，仅保存 JSON 元数据；同时让所有发现链接拒绝 localhost、元数据地址和非全局 IP 字面量。

**Acceptance criteria:**

- [ ] GDELT 固定样本映射 `url`、`title`、`seendate`、`domain`，并产生规范化小写 `publisher_key`。
- [ ] GDELT 条目缺少域名或合法 HTTPS 原文链接时丢弃，不请求原文。
- [ ] 支持 GDELT 的紧凑时间格式；无效时间保留为 `None`，不导致整个来源失败。
- [ ] `candidate_url` 拒绝 userinfo、localhost、云元数据 host、私网/保留/链路本地 IP 字面量和非 HTTP(S) URL。
- [ ] API 响应体仍受已有大小、超时和请求次数限制。

**Verification:**

- [ ] `python -m pytest tests/unit/test_collectors.py -q`
- [ ] `python -m ruff check src/commerce_agent/ingestion/collectors/api.py src/commerce_agent/ingestion/collectors/base.py tests/unit/test_collectors.py`

**Dependencies:** Task 4

**Files likely touched:**

- Modify: `src/commerce_agent/ingestion/models.py`
- Modify: `src/commerce_agent/ingestion/collectors/api.py`
- Modify: `src/commerce_agent/ingestion/collectors/base.py`
- Create: `tests/fixtures/ingestion/gdelt_articles.json`
- Modify: `tests/unit/test_collectors.py`

**Estimated scope:** Medium, 5 files

**Commit:** `feat: collect safe GDELT article metadata`

## Task 7: 将媒体出处映射为统一提取结果

**Description:** 让内容提取器为每条媒体材料产出 `publisher_key`、`attribution` 和 `content_scope`。直接来源使用来源配置，GDELT 使用条目级出版机构；缺少必要出处时该条材料安全失败。

**Acceptance criteria:**

- [ ] 官方来源不需要媒体字段，现有提取行为不变。
- [ ] 直接媒体条目继承来源级出版机构与署名。
- [ ] GDELT 条目使用适配器产生的出版机构并保留 GDELT 署名。
- [ ] media 条目缺少最终 `publisher_key` 时产生固定、无敏感信息的 `missing_publisher_identity` 错误。

**Verification:**

- [ ] `python -m pytest tests/unit/test_content_extraction.py -q`
- [ ] `python -m ruff check src/commerce_agent/ingestion/extract.py tests/unit/test_content_extraction.py`

**Dependencies:** Task 6

**Files likely touched:**

- Modify: `src/commerce_agent/ingestion/extract.py`
- Modify: `tests/unit/test_content_extraction.py`

**Estimated scope:** Small, 2 files

**Commit:** `feat: extract normalized media provenance`

## Task 8: 无破坏地持久化每个版本的媒体出处

**Description:** 新建 `document_provenance` 一对一表，通过 `document_version_id` 保存出版机构、署名和内容范围。`create_all` 可在现有 SQLite 中只新增表，不改动已有列和用户数据。

**Acceptance criteria:**

- [ ] 新表以 `document_version_id` 为主键/外键，包含非空 `publisher_key`、`attribution`、`content_scope`。
- [ ] media 新版本与出处在同一事务写入；重复版本不会产生重复出处。
- [ ] official 版本允许没有出处行。
- [ ] 现有数据库执行 `Database.create_schema()` 后新增表且已有绑定、文档和日报数据保持不变。
- [ ] 新版本仍恰好创建一个分析任务。

**Verification:**

- [ ] `python -m pytest tests/integration/test_ingestion_repository.py tests/integration/test_ingestion_pipeline.py -q`
- [ ] `python -m ruff check src/commerce_agent/persistence/models.py src/commerce_agent/persistence/ingestion.py src/commerce_agent/ingestion/service.py`

**Dependencies:** Task 7

**Files likely touched:**

- Modify: `src/commerce_agent/persistence/models.py`
- Modify: `src/commerce_agent/persistence/ingestion.py`
- Modify: `src/commerce_agent/ingestion/service.py`
- Modify: `tests/integration/test_ingestion_repository.py`
- Modify: `tests/integration/test_ingestion_pipeline.py`

**Estimated scope:** Medium, 5 files

**Commit:** `feat: persist per-document media provenance`

## Task 9: 按出版机构加载并计算交叉验证

**Description:** 把出处加载进 `AnalysisCandidate`，将现有“不同 source_id”计数改为“不同 publisher_key”计数，使采集渠道不再冒充独立证据来源。

**Acceptance criteria:**

- [ ] media 分析候选包含非空 `publisher_key`、署名和内容范围；official 候选保持兼容。
- [ ] 同一事件的 GDELT 和直接 RSS 若 `publisher_key` 相同，只计一个来源。
- [ ] 两个不同媒体出版机构计为两个来源；官方来源继续按当前逻辑处理。
- [ ] 已完成分析、当前批次分析和报告重载使用一致语义。

**Verification:**

- [ ] `python -m pytest tests/integration/test_intelligence_repository.py -q`
- [ ] `python -m ruff check src/commerce_agent/intelligence/models.py src/commerce_agent/intelligence/repository.py tests/integration/test_intelligence_repository.py`

**Dependencies:** Task 8

**Files likely touched:**

- Modify: `src/commerce_agent/intelligence/models.py`
- Modify: `src/commerce_agent/intelligence/repository.py`
- Modify: `tests/integration/test_intelligence_repository.py`

**Estimated scope:** Medium, 3 files

**Commit:** `feat: corroborate media by publisher identity`

## Task 10: 限制单一媒体出版机构的置信度

**Description:** 在证据评分器中落实单媒体 70 分上限，并证明两个不同出版机构可以解除上限。

**Acceptance criteria:**

- [ ] 单一 media 出版机构最终分数不超过 70。
- [ ] 两个不同媒体出版机构按现有分项正常计分。
- [ ] official 候选不受媒体上限影响。
- [ ] 分析服务把 Task 9 的出版机构计数原样传给评分器。

**Verification:**

- [ ] `python -m pytest tests/unit/test_intelligence_evidence.py tests/unit/test_intelligence_service.py -q`
- [ ] `python -m ruff check src/commerce_agent/intelligence/evidence.py tests/unit/test_intelligence_evidence.py tests/unit/test_intelligence_service.py`

**Dependencies:** Task 9

**Files likely touched:**

- Modify: `src/commerce_agent/intelligence/evidence.py`
- Modify: `tests/unit/test_intelligence_evidence.py`
- Modify: `tests/unit/test_intelligence_service.py`

**Estimated scope:** Small, 3 files

**Commit:** `feat: cap uncorroborated media confidence`

## Checkpoint B: 媒体证据链

- [ ] Task 4–10 的定向测试全部通过。
- [ ] `python -m pytest tests/unit/test_source_registry.py tests/unit/test_collectors.py tests/integration/test_ingestion_pipeline.py tests/integration/test_intelligence_pipeline.py -q` 通过。
- [ ] 一个媒体来源失败时，另一个来源仍完成并生成自己的分析任务。
- [ ] 不存在从 YAML 动态导入代码或启用 `full_text` 的路径。

## Task 11: 注册 Amazon、eBay 与 GDELT 的真实来源

**Description:** 将第一批真实端点配置为受审来源。eBay 保持启用；Amazon 与 GDELT 先以关闭状态进入注册表，现场冒烟通过后再启用，避免提交一个未经本机验证的自动运行源。

**Planned source definitions:**

- Amazon entry URL: `https://developer-docs.amazon/sp-api/changelog.rss`
- Amazon permission evidence: `https://developer-docs.amazon/sp-api/changelog/october-2022-sp-api-release-announcement`
- eBay entry URL: `https://www.ebayinc.com/stories/news/rss/`
- GDELT entry URL:
  `https://api.gdeltproject.org/api/v2/doc/doc?query=%28Amazon%20OR%20TEMU%20OR%20SHEIN%20OR%20AliExpress%20OR%20Shopee%20OR%20eBay%20OR%20Coupang%20OR%20Ozon%20OR%20Joybuy%20OR%20%22TikTok%20Shop%22%29%20%28policy%20OR%20compliance%20OR%20regulation%20OR%20recall%20OR%20lawsuit%20OR%20tariff%20OR%20seller%29&mode=artlist&format=json&maxrecords=50&timespan=1d&sort=datedesc`
- GDELT collector mapping: `items_path=$.articles`, `url_field=$.url`, `title_field=$.title`, `published_at_field=$.seendate`, `publisher_field=$.domain`, `item_limit=50`.

**Acceptance criteria:**

- [ ] Amazon 使用当前官方域名的 SP-API changelog RSS；合规说明引用 Amazon 官方明确发布的 RSS 订阅说明。
- [ ] eBay Newsroom RSS 配置不回退到网页抓取。
- [ ] GDELT 使用 `mode=artlist`、`format=json`、`sort=datedesc`、`timespan=1d`、`maxrecords=50`，查询同时包含十个平台名称和风险词组。
- [ ] GDELT 配置为 `metadata_only`，逐条出版机构字段为 `domain`，绝不请求文章 URL。
- [ ] 受控烟测最多对每个来源发起一个列表/API 请求；网络测试默认跳过，只有 `RUN_PUBLIC_SOURCE_SMOKE=1` 时运行。

**Verification:**

- [ ] `python -m pytest tests/unit/test_source_registry.py tests/smoke/test_public_sources.py -q`
- [ ] `python -m commerce_agent.ingestion_cli sources list`
- [ ] 人工检查 YAML 中无密钥、Cookie、授权头、登录 URL 或空格未编码的查询参数。

**Dependencies:** Task 3, Task 6, Task 10

**Files likely touched:**

- Modify: `src/commerce_agent/sources/public_sources.yaml`
- Modify: `tests/unit/test_source_registry.py`
- Modify: `tests/smoke/test_public_sources.py`

**Estimated scope:** Medium, 3 files

**Commit:** `feat: register reviewed Amazon and GDELT sources`

## Task 12: 在日报中完整呈现风险、置信度、依据、动作和原文

**Description:** 扩展日报 payload，使每个“已核验”或“待核验”项目都有用户要求的五项关键信息，并保留 AI 一句话提炼和平台覆盖。

**Acceptance criteria:**

- [ ] 每条详情显示风险等级、证据分数、核验状态、AI 摘要、至少一个可定位判断依据、建议动作、署名和原文 URL。
- [ ] 单媒体 60–70 分条目标为“待核验”，措辞不得暗示平台已经确认。
- [ ] 保守/默认/激进只改变建议强度，不改变证据分数和来源状态。
- [ ] 无符合阈值项目时仍生成健康卡片，不虚构更新。
- [ ] 飞书渲染继续满足消息长度与安全输出限制。

**Verification:**

- [ ] `python -m pytest tests/unit/test_intelligence_reports.py tests/unit/test_intelligence_delivery.py tests/integration/test_intelligence_pipeline.py -q`
- [ ] `python -m ruff check src/commerce_agent/intelligence/reports.py tests/unit/test_intelligence_reports.py`

**Dependencies:** Task 10

**Files likely touched:**

- Modify: `src/commerce_agent/intelligence/reports.py`
- Modify: `tests/unit/test_intelligence_reports.py`
- Modify: `tests/unit/test_intelligence_delivery.py`
- Modify: `tests/integration/test_intelligence_pipeline.py`

**Estimated scope:** Medium, 4 files

**Commit:** `feat: render evidence-rich daily report items`

## Checkpoint C: 离线完整链路

- [ ] `python -m pytest -q` 全部通过，公开网络烟测仍默认跳过。
- [ ] `python -m ruff check .` 通过。
- [ ] 离线固定样本完成采集、去重、出处持久化、AI 模拟分析和日报预览。
- [ ] `git diff --check` 无格式错误；暂存区秘密扫描无凭据。

## Task 13: 真实采集、飞书投递与本机定时运行

**Description:** 在测试全部通过后执行现场验收。先停止旧机器人以避免 SQLite 和调度冲突，使用进程级 DoH 依次验证来源，再分析、预览、人工确认发送，最后按安全开关重启机器人。

**Acceptance criteria:**

- [ ] eBay、Amazon 和 GDELT 都有明确健康结果；通过的来源最终启用，失败来源保持关闭并记录固定错误码。
- [ ] 至少一条真实材料新增或更新，并完成 AI 分析；不得插入伪造生产数据。
- [ ] 日报预览至少选中一条真实项目；若当日确无更新，使用受控的最长三个月历史窗口重新查询 GDELT，而不是伪造内容。
- [ ] 用户检查预览结果后，带 `--confirm` 发送一次，outbox 状态为 sent。
- [ ] 机器人以采集、分析、日报开启，告警和 QA 关闭的配置重新上线，并能响应“帮助”“状态”。

**Verification and commands:**

- [ ] 仅对当前机器人 PID 做只读确认后停止该进程，不结束其他 Python 进程。
- [ ] 当前 PowerShell 进程设置：`$env:INGESTION_DNS_MODE='cloudflare_doh'`
- [ ] 烟测设置：`$env:RUN_PUBLIC_SOURCE_SMOKE='1'; python -m pytest tests/smoke/test_public_sources.py -q`
- [ ] 依次执行：
  - `python -m commerce_agent.ingestion_cli run --source ebay-newsroom-rss`
  - `python -m commerce_agent.ingestion_cli run --source amazon-sp-api-changelog-rss`
  - `python -m commerce_agent.ingestion_cli run --source media-gdelt-platform-risk-discovery`
- [ ] 执行分析：`python -m commerce_agent.intelligence_cli analyze --pending --limit 100`，重复到 `claimed=0`。
- [ ] 按上海 09:00 窗口计算报告日期，再执行：`python -m commerce_agent.intelligence_cli report preview --date <YYYY-MM-DD>`。
- [ ] 用户确认预览后执行：`python -m commerce_agent.intelligence_cli report send --date <YYYY-MM-DD> --confirm`。
- [ ] 执行：`python -m commerce_agent.intelligence_cli health` 和 `python -m commerce_agent.ingestion_cli health`。
- [ ] 重启时仅在该进程设置：
  - `INGESTION_DNS_MODE=cloudflare_doh`
  - `INGESTION_SCHEDULER_ENABLED=true`
  - `INTELLIGENCE_ANALYSIS_ENABLED=true`
  - `INTELLIGENCE_DAILY_REPORT_ENABLED=true`
  - `INTELLIGENCE_ALERTS_ENABLED=false`
  - `INTELLIGENCE_QA_ENABLED=false`

**Dependencies:** Tasks 1–12 and Checkpoint C

**Files likely touched:**

- Create: `docs/runbooks/local-live-intelligence.md`
- Modify after live result only: `src/commerce_agent/sources/public_sources.yaml`

**Estimated scope:** Medium operational task; requires an explicit user review immediately before the single send command.

**Commit after successful smoke:** `chore: enable verified live intelligence sources`

## Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Amazon RSS 从 `.com` 跳转到 `.amazon` 或暂时不可用 | Medium | 配置当前官方最终 host；仍不允许跨 host 隐式跳转；烟测失败则保持关闭 |
| GDELT 返回噪声或重复媒体文章 | Medium | 平台词与风险词双条件、24 小时窗口、原文 URL 规范化、`publisher_key` 去重、单媒体 70 分上限 |
| Clash/Mihomo fake-IP 干扰公网校验 | High | 固定 Cloudflare DoH；失败关闭；不回退系统 DNS；公网校验不放宽 |
| 新表与已有 SQLite 数据库不兼容 | Medium | 只新增 `document_provenance` 表；先备份数据库；`create_all` 后验证现有绑定与记录计数 |
| AI 对只有标题的媒体元数据过度推断 | High | 元数据来源只能待核验；证据引用必须存在于保存 JSON；日报明确显示不确定性和原文链接 |
| 多渠道同一媒体被重复算作独立证据 | High | 计数使用 `publisher_key`，不用 `source_id`；GDELT domain 与直接来源归一化 |
| 测试发送重复投递 | Medium | 预览与发送分离；`--confirm` 必需；日报唯一键和 outbox 幂等键继续生效 |

## Rollback

- 任何现场来源异常：把对应 YAML 的 `enabled` 改回 `false` 并重启机器人，不删除已采集文档。
- DoH 异常：停止采集调度，将新进程的 `INGESTION_DNS_MODE` 改回 `system`；不关闭 URL 安全策略。
- 分析异常：保持材料与 pending job，关闭 `INTELLIGENCE_ANALYSIS_ENABLED`，修复后重试。
- 投递异常：保持 outbox 记录，禁止重复手工发送，先通过 health 检查现有状态。
- 数据库升级前创建 SQLite 文件副本；回滚代码时保留新增表不会影响旧代码查询。

## Definition of Done

- [ ] 每个任务的定向测试和检查通过并形成原子提交。
- [ ] 全量 pytest 与 Ruff 通过，无新增未解释警告。
- [ ] 暂存差异中没有 `.env`、密钥、Cookie、Token 或群 ID。
- [ ] 三个真实来源均有可审计结果，失败来源安全关闭。
- [ ] 至少一条真实材料可追溯到原文和出版机构。
- [ ] AI 结果及日报包含风险、置信度、依据、建议动作和原文。
- [ ] 单次确认发送成功，机器人重新在线并保持安全开关组合。

## Open Questions

无。未来新增具体媒体时，每个媒体单独完成许可审核和启用决策，不阻塞本计划。
