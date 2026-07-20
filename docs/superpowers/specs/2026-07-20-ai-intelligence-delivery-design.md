# AI 跨境电商情报交付设计

- 日期：2026-07-20
- 状态：待用户书面复核
- 范围：AI 分析、每日飞书日报、中高风险预警、群内有据问答
- 前置能力：公开来源采集、版本化入库、飞书群绑定、DeepSeek 连通性测试

## 1. 目标

在现有公开资讯采集底座上增加一条可审计、可重试、不会重复计费或重复推送的情报交付流水线：

1. 对每个新的文章版本执行一次 DeepSeek 结构化分析；
2. 每天北京时间 09:00 向当前有效绑定群推送“混合决策版”日报；
3. 每轮采集后的 10 分钟内自动推送符合条件的中、高风险预警；
4. 群成员可在同一飞书话题中连续追问，只依据已入库且当前合规允许的内容回答；
5. 所有事实结论都能回溯到原文，证据不足时明确拒绝作出确定性判断。

第一版不以“平台数量”凑内容。当前只有 eBay Newsroom RSS 处于 `allowed + enabled`，系统应如实显示其他平台“无已验证更新”或“等待合规来源接入”。未来增加来源不改变本设计中的 AI、日报、预警和问答接口。

## 2. 已确认的产品决策

- 日报：每天 09:00，时区 `Asia/Shanghai`，覆盖前一天 09:00 至当天 09:00。
- 空日报：没有可信新增时仍发送简短健康日报。
- 日报布局：B 型“混合决策版”，顺序为“AI 今日提炼 → 风险与待办 → 平台动态 → 今日建议”。
- 日报篇幅：动态选择 5–15 条；不凑数，不可信内容不进入正文。
- 即时预警：中风险和高风险均自动推送；低风险只进入日报。
- 采集频率：保持每 120 分钟；预警时效目标为采集完成后 10 分钟内。
- 问答语料：只使用已入库、已分析且当前合规状态为 `allowed` 的资讯，不开放互联网搜索。
- 问答上下文：同一飞书话题最近 6 轮、30 分钟，仅保存在进程内存，重启即清除。
- 基础设施：使用现有 Python 进程和 SQLite，不增加 Redis、消息队列服务器或向量数据库。
- 上线控制：AI、日报、预警、问答均使用独立开关且默认关闭；不自动修改用户 `.env`。

## 3. 非目标

本阶段不包含：

- 新增或放宽任何公开来源的合规状态；
- 登录态采集、验证码处理、付费墙绕过或浏览器生产采集；
- 长期个人画像、长期保存群成员问答内容；
- 开放互联网搜索或把模型自身知识当作平台事实；
- 独立管理后台、移动端应用或多租户计费；
- 独立向量数据库、跨节点任务队列或多服务器部署；
- 自动开启生产推送或自动修改 `.env`。

## 4. 总体架构

采用 SQLite 持久化任务流水线：

```text
文章版本入库
  → AI 分析任务
  → DeepSeek 结构化输出
  → 证据校验与风险规则复核
  ├→ 中/高风险预警 Outbox
  ├→ 每日日报内容池
  └→ 群内问答检索索引
  → 飞书幂等发送与审计
```

采集、AI、评分、报告和发送各自拥有独立状态。某一步失败只重试该步：飞书发送失败不能重新调用 AI，AI 失败不能破坏已经入库的文章版本。

### 4.1 组件边界

| 组件 | 职责 | 主要依赖 |
|---|---|---|
| `AnalysisJobRepository` | 原子领取、续期、完成和重试 AI 任务 | SQLite |
| `IntelligenceAnalyzer` | 构造受限输入、调用 DeepSeek、校验结构化输出 | DeepSeek 端口 |
| `EvidenceScorer` | 计算证据可信度，验证事实是否锚定原文 | 文章版本、来源元数据 |
| `RiskPolicy` | 使用确定性规则复核风险等级与自动推送资格 | 分析结果 |
| `DailyReportComposer` | 选择时间窗内容、排序、生成 B 型日报模型 | 分析仓储 |
| `AlertComposer` | 合并中风险、生成单条高风险卡片 | 风险结果 |
| `DeliveryOutbox` | 幂等发送、退避重试、记录飞书消息结果 | 飞书发送端口 |
| `CorpusRetriever` | 平台、时间、风险和中文词片段混合检索 | SQLite |
| `QaService` | 基于检索证据回答、校验引用、管理短期上下文 | DeepSeek 端口、内存 TTL |

每个组件通过小型协议或不可变数据对象交互，不直接访问其他组件的内部字段。

## 5. 数据模型

### 5.1 `analysis_jobs`

- `id`
- `document_version_id`，唯一
- `status`：`pending | running | retry_wait | succeeded | failed`
- `attempt_count`
- `next_attempt_at`
- `lease_token`
- `lease_expires_at`
- `error_code`
- `created_at`、`updated_at`

任务使用带 token 的数据库租约原子领取。旧 worker 不能完成或释放已被新 worker 回收的租约。单篇最多尝试两次；第二次失败进入稳定失败状态，等待人工或显式重跑。

### 5.2 `document_analyses`

- `id`
- `document_version_id`，唯一
- `schema_version`
- `prompt_version`
- `model_name`
- `headline_zh`
- `summary_zh`
- `event_type`
- `risk_level`
- `evidence_confidence`
- `event_fingerprint`
- `structured_payload`
- `analyzed_at`

`structured_payload` 保存通过严格模式校验的 JSON，不保存模型思维过程。文章版本内容不变时不重新分析；提示模板升级不自动回刷历史，必须运行显式 backfill 命令。

### 5.3 `daily_reports`

- `id`
- `group_id`
- `report_date`
- `window_start`、`window_end`
- `status`：`draft | previewed | queued | sent | failed`
- `selected_analysis_ids`
- `report_payload`
- `created_at`、`sent_at`

数据库唯一约束为 `(group_id, report_date)`。重新生成草稿可更新未发送记录；已发送记录不可覆盖。

### 5.4 `delivery_outbox`

- `id`
- `idempotency_key`，唯一
- `group_id`
- `message_kind`：`daily_report | medium_alert_batch | high_alert | qa_answer`
- `payload`
- `status`：`pending | sending | retry_wait | sent | failed | skipped`
- `attempt_count`
- `next_attempt_at`
- `safe_error_code`
- `feishu_message_id`
- `created_at`、`sent_at`

Outbox 不保存访问令牌、Authorization、Cookie、完整请求 URL 或群绑定码。

## 6. AI 输入与输出契约

### 6.1 输入

模型只接收：

- 当前不可变文章版本的规范化正文；
- 标题、作者、原发布时间；
- 平台、站点、地区、来源名称和来源等级；
- 固定系统指令与 JSON Schema。

模型不接收 `.env`、数据库连接、飞书凭据、历史日志、未选择的其他文章或用户个人信息。原文置于明确的数据边界中，并声明其中任何提示、命令或角色要求均是不可信内容。

### 6.2 结构化输出

```python
class AnalysisResult(BaseModel):
    headline_zh: str
    summary_zh: str
    event_type: EventType
    platforms: tuple[Platform, ...]
    regions: tuple[str, ...]
    affected_seller_types: tuple[str, ...]
    effective_at: datetime | None
    risk_level: RiskLevel
    impact: str
    rationale: tuple[EvidenceClaim, ...]
    action_items: tuple[ActionItem, ...]
    uncertainties: tuple[str, ...]
    tags: tuple[str, ...]
```

约束：

- 中文摘要 80–150 字；
- 未知日期、金额、范围必须返回 `null` 或列入 `uncertainties`；
- 每条 `rationale` 必须引用当前文章中的短证据片段或可定位文本范围；
- 不允许自由新增平台、站点、风险等级或事件类型；
- 不返回 Markdown 代码块，不返回模型思维过程；
- JSON 校验失败可用同一输入修复一次，仍失败则记录 `invalid_model_output`。

## 7. 风险等级与证据可信度

风险等级表示事件潜在影响；证据可信度表示当前材料能否支持该判断，两者必须分开显示。

### 7.1 风险类别

确定性规则至少覆盖：

- 账户停用、封号、绩效执法；
- 费用新增或上涨；
- 税务、申报和合规期限；
- 物流中断、仓储限制和配送能力变化；
- 禁售、召回、商品资质和知识产权限制；
- 平台政策生效日期、迁移截止日期；
- 大范围 API、支付或结算故障。

规则命中可提高模型风险等级，但模型不能降低规则确定的最低等级。规则与模型严重冲突时结果进入“待核实”，不能自动预警。

### 7.2 证据可信度

满分 100，由代码计算，而不是采用模型自报概率：

- 来源可信度：30；
- 证据锚定完整性：25；
- 正文提取完整性：15；
- 明确范围、生效时间或数值：10；
- 多来源印证：10；
- Schema 与事实一致性检查：10。

官方单一来源即使没有交叉印证，仍可在其他条件完整时达到 90。

自动处理阈值：

- 中风险或高风险，可信度 `>= 75`：允许自动预警；
- 可信度 `60–74`：进入日报“待核实”，不即时推送；
- 可信度 `< 60`：保留分析记录，不进入预警；
- 低风险：不即时推送，可参与日报排序。

## 8. 日报设计

### 8.1 时间与内容

- Cron：每日 09:00，`Asia/Shanghai`；
- 窗口：前一天 09:00（含）至当天 09:00（不含）；
- 目标篇幅：5–15 条；不足 5 条时不补低可信内容；
- 无可信新增时仍生成健康日报；
- 一条事件存在多篇报道时按 `event_fingerprint` 聚合，优先引用官方来源。

### 8.2 B 型混合决策卡片

1. AI 今日提炼；
2. 风险与待办；
3. 按平台展示动态；
4. 今日建议；
5. 数据覆盖说明与原文链接。

卡片明确区分“无已验证更新”和“该平台尚无合规启用来源”。卡片超长时保留 Top 15；仍超过飞书限制时退化为安全纯文本摘要与链接列表。

日报幂等键为 `daily:{group_id}:{report_date}`。一天内同一群只能成功发送一次。

## 9. 即时预警设计

- 高风险：单独红色卡片；
- 中风险：同一分析轮次合并为一张橙色卡片；
- 低风险：只进入日报；
- 每条内容显示风险等级、证据可信度、事件摘要、影响范围、判断依据、建议动作、负责人类型、建议期限、原文和不确定性；
- 相同事件 24 小时内不重复；风险升级或文章出现实质新版本时可再次发送。

事件指纹由平台、事件类型、规范化主体、生效时间和关键事实生成。预警幂等键包含群、事件指纹、风险等级与 24 小时时间桶。

## 10. 飞书交付与错误处理

第一版只使用当前有效群绑定。没有绑定时 Outbox 记录 `no_active_binding` 并安全跳过，不猜测目标群。

发送重试：

- 第一次失败后 1 分钟；
- 第二次失败后 5 分钟；
- 第三次失败后 30 分钟；
- 三次仍失败进入 `failed`，不重新调用 AI。

已成功发送的 idempotency key 永不自动重发。飞书响应日志只保存安全状态码、受控错误码和内部记录 ID。

## 11. 群内有据问答

### 11.1 检索

只检索：

- 已有成功 `document_analyses`；
- 对应来源当前 `compliance=allowed`；
- 默认最近 30 天；
- 用户指定的平台、站点、风险等级和时间条件。

检索采用 SQLite 本地混合排序：元数据过滤、关键词、中文字符片段、标题权重、时间衰减和风险权重。第一版不增加外部 embedding API 或向量数据库。

### 11.2 回答

- 用户 `@机器人 + 问题` 后先收到线程内确认；
- 回答只能基于检索结果；
- 每个事实结论必须带 `[1]` 形式引用；
- 文末列出 1–5 个来源的标题、发布方、发布时间和原文链接；
- 引用校验失败则不发送答案；
- 资料不足时固定回答“当前入库资料不足以判断”，并建议平台或时间过滤条件；
- 原文和用户消息都不能覆盖系统提示或触发工具调用。

### 11.3 短期上下文

上下文键为 `(chat_id, thread_id)`，最多 6 轮，闲置 30 分钟过期。只保存在进程内存，不写数据库；进程关闭时清空。上下文仅用于解释代词和连续筛选，不能作为事实来源。

## 12. 调度与运行时

现有采集调度保持每 120 分钟。新增稳定 job ID：

- `intelligence-analysis-drain`：每 5 分钟领取待分析任务，`max_instances=1`；
- `intelligence-delivery-retry`：每 1 分钟处理到期 Outbox，`max_instances=1`；
- `intelligence-daily-report`：每日 09:00，`Asia/Shanghai`，`max_instances=1`。

分析并发最多 2。分析 drain 的 5 分钟频率保证采集完成后 10 分钟内可生成预警。运行时关闭顺序为：停止新 job → 等待或取消安全任务 → 关闭飞书发送器 → 关闭 DeepSeek → 释放数据库。

## 13. 配置

新增安全默认值：

```dotenv
INTELLIGENCE_ANALYSIS_ENABLED=false
INTELLIGENCE_DAILY_REPORT_ENABLED=false
INTELLIGENCE_ALERTS_ENABLED=false
INTELLIGENCE_QA_ENABLED=false
INTELLIGENCE_TIMEZONE=Asia/Shanghai
INTELLIGENCE_DAILY_HOUR=9
INTELLIGENCE_AI_CONCURRENCY=2
INTELLIGENCE_EVIDENCE_THRESHOLD=75
INTELLIGENCE_CONTEXT_TTL_MINUTES=30
INTELLIGENCE_QA_MAX_TURNS=6
```

修改 `.env` 后必须重启。实现和测试不得读取、显示或自动修改现有凭据值。

## 14. 管理员命令

```powershell
# 分析待处理的新文章版本
python -m commerce_agent.intelligence_cli analyze --pending --limit 10

# 生成日报草稿，不发送
python -m commerce_agent.intelligence_cli report preview --date 2026-07-21

# 将已经预览的日报手动发送到当前绑定群
python -m commerce_agent.intelligence_cli report send --date 2026-07-21 --confirm

# 预览最近一轮符合条件的预警
python -m commerce_agent.intelligence_cli alerts preview --since-hours 24

# 查看 AI、日报、预警和 Outbox 健康状态
python -m commerce_agent.intelligence_cli health
```

命令错误码：0 成功、2 参数或目标错误、3 运行/部分失败。所有输出使用安全错误码，不回显查询正文、群绑定码或秘密。

## 15. 项目结构

```text
src/commerce_agent/intelligence/
  models.py          # 结构化分析、风险、报告和发送对象
  repository.py      # AI 任务、分析、报告与 Outbox 持久化
  analyzer.py        # DeepSeek 结构化分析适配
  evidence.py        # 证据锚定与可信度
  risk.py            # 确定性风险规则
  reports.py         # 日报与预警组合
  delivery.py        # 飞书卡片和 Outbox worker
  retrieval.py       # SQLite 本地混合检索
  qa.py              # 有据问答与短期上下文
  scheduler.py       # AI drain、日报、重试 job
src/commerce_agent/intelligence_cli.py
tests/unit/test_intelligence_*.py
tests/integration/test_intelligence_pipeline.py
docs/operations/intelligence-delivery-runbook.md
```

## 16. 代码风格

- Python 3.11/3.12；
- 异步 I/O，网络和模型调用通过注入端口；
- 领域对象不可变，数据库事务短小；
- 使用枚举和受控错误码，不在日志中拼接异常正文；
- 单个模块保持单一职责，公共接口不依赖私有字段；
- Ruff 规则与现有项目一致。

## 17. 测试策略

### 17.1 单元测试

- JSON Schema、未知字段和模型幻觉拒绝；
- 原文提示注入不能改变输出契约；
- 证据片段必须存在于原文；
- 风险规则只能提高最低风险；
- 可信度阈值 60/75 边界；
- 日报 5–15 条排序、空日报和平台覆盖措辞；
- 事件指纹、24 小时去重和风险升级；
- 飞书卡片长度与纯文本退化；
- 问答引用完整性、拒答和 30 分钟上下文过期；
- 配置默认关闭和数值边界。

### 17.2 集成测试

- 两个 worker 原子领取同一分析任务，只产生一次 DeepSeek 调用；
- 同一文章版本只产生一份分析；
- AI 失败重试不产生重复记录；
- 报告生成、Outbox、发送成功与发送重试全链路；
- 飞书失败不重新调用 AI；
- 一天同群只成功发送一份日报；
- 相同预警 24 小时不重复；
- 群问答只检索当前合规内容并附有效引用。

### 17.3 离线端到端测试

使用假 DeepSeek、假飞书、临时 SQLite 和固定文章夹具，覆盖：

`文章入库 → AI 分析 → 风险判断 → 日报/预警 → 飞书发送 → 群内问答`

默认测试严禁真实网络。真实 DeepSeek 和飞书只通过显式 smoke 开关运行，且使用严格调用预算。

### 17.4 回归门禁

```powershell
python -m pytest -v
python -m ruff check .
python -m compileall -q src tests
git diff --check
```

原有帮助、状态、绑定、AI 测试、采集、CLI、调度、安全和幂等测试必须继续通过。

## 18. 运行安全与可观测性

记录的指标包括：待分析数、分析成功/失败/重试、模型调用次数、结构校验失败、风险等级分布、日报生成/发送、Outbox 重试、问答拒答率和引用校验失败。

日志只记录内部 ID、状态、耗时、token 数量区间和受控错误码。禁止记录：

- API Key、Authorization、Cookie、SDK ticket；
- 完整原文、完整提示、完整模型输出；
- 飞书用户标识查询串、群绑定码；
- 含秘密或用户内容的异常正文。

文章正文和用户提问均视为不可信输入。AI 无工具权限；问答模型不能访问网络、文件系统或配置。

## 19. 分阶段上线

1. 创建数据库表与默认关闭配置；
2. 使用离线夹具验证完整流水线；
3. 对一篇已入库文章执行一次真实 DeepSeek 分析；
4. 生成日报预览，不发送；
5. 人工核对摘要、风险、可信度、依据、动作和原文；
6. 手动发送到当前测试群；
7. 验证中、高风险去重与引用；
8. 开启群内问答；
9. 经用户明确批准后，分别开启日报和预警自动推送。

每一步可单独回退。关闭新开关不影响现有采集和飞书命令。

## 20. 边界

### 始终执行

- 新行为先写失败测试；
- 所有模型输出严格校验；
- 所有事实回答附有效来源；
- 所有发送使用数据库幂等键；
- 任何真实调用使用显式开关和调用预算；
- 提交前运行完整测试和安全扫描。

### 必须先询问

- 开启自动日报、自动预警或生产 AI drain；
- 修改当前来源合规状态；
- 增加新的外部服务、embedding API 或数据库；
- 回刷全部历史文章；
- 长期保存用户问答内容。

### 永不执行

- 自动修改用户 `.env`；
- 无引用生成平台事实；
- 把模型自报概率直接当作可信度；
- 记录秘密、完整查询串或完整用户内容；
- 因 AI 或飞书失败重复创建文章版本；
- 绕过来源访问限制或扩大当前合规授权。

## 21. 验收标准

- 新文章版本在一个分析周期内生成至多一份结构化分析；
- AI 输出不包含 Schema 外字段，所有证据可在对应原文中定位；
- 中、高风险且可信度 `>=75` 才自动预警；
- 日报每天 09:00 生成，动态 5–15 条，无内容时仍生成健康日报；
- 同群同日只成功发送一次日报；
- 相同事件 24 小时不重复预警，风险升级可重发；
- 飞书重试不重新调用 AI；
- 问答只使用当前合规入库内容，每个事实结论有有效引用；
- 同一话题保留最多 6 轮、30 分钟内存上下文，重启后清空；
- 四个生产开关默认均为 `false`；
- 原有 329 项通过测试及新增测试全部通过；
- 独立正确性与安全复审无 Critical 或 Important；
- 未经用户明确批准，不开启自动日报或预警。

## 22. 已延期事项

- 更多平台的合规来源扩充；
- 统一受限的生产浏览器采集；
- embedding 与向量数据库；
- 长期对话记忆和个人画像；
- 多服务器 worker 与外部消息队列；
- 管理后台和可视化分析面板。

这些事项不影响本阶段端到端 MVP 的完成标准。
