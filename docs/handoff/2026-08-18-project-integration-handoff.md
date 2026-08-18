# 跨境电商飞书情报智能体：项目集成交接文档

> 交接日期：2026-08-18
> 最近更新：2026-08-18 11:30（重点媒体/官方来源正式接入后）
> 当前项目：`cross-border-commerce-agent` 0.1.0
> 当前运行方式：Windows 本地常驻进程 + 飞书长连接 + APScheduler + SQLite
> 目标项目：尚未指定，本文按“可接入任意 Python 3.11/3.12 后端”编写

## 1. 交接结论

本项目已经具备一条可运行的完整链路：公开来源采集、Firecrawl 优先抓取、本地正文提取与去重、DeepSeek 结构化分析、风险/置信度/依据/建议生成、日报与预警编排、飞书卡片投递、群绑定和有据问答。

推荐集成方式是先把本项目作为目标系统的一个**独立情报子系统/sidecar worker**保留，而不是直接把全部内部模块复制进目标项目。原因是它同时拥有长连接、定时器、数据库状态、幂等投递和外部抓取资源；独立运行能减少调度器重复、数据库表冲突和飞书重复推送。目标项目通过一层稳定适配接口调用它，后续再决定是否合并进同一进程。

当前没有公开 HTTP API。若目标项目需要在页面上触发“立即采集、生成日报、发送日报、查询健康状态”，应先实现本文第 8 节定义的薄适配层，不能直接依赖 `_build_ingestion`、`_build_intelligence` 等私有函数。

## 2. 已实现能力与边界

### 2.1 已实现

- 飞书企业自建应用机器人长连接接收消息。
- 单个有效飞书群绑定、群风险策略配置和投递前有效性检查。
- 十个平台的数据模型：Amazon、TEMU、SHEIN、AliExpress、Shopee、eBay、Coupang、Ozon、Joybuy、TikTok Shop。
- YAML 来源登记、合规状态、抓取级别、站点、语言和速率配置。
- RSS、HTML、API、Sitemap 采集器；浏览器采集代码保留但生产强制关闭。
- 对 `allowed + enabled` 来源先执行 Firecrawl 入口页抓取，同时保留原生采集；原生文章优先，原生无结果时使用 Firecrawl 文档恢复。
- URL/内容去重、版本、来源溯源、快照和来源健康记录。
- DeepSeek 非思考模式的结构化情报分析。
- 保守、默认、激进三档风险策略。
- 日报、即时预警、问答和手工官方公告提交。
- 飞书卡片超长时自动降级为文本；出站消息有幂等键和重试状态。
- 09:00 日报、08:40 预生成、启动后错过 09:00 时自动补偿。
- Windows 登录启动、每日 08:30 唤醒启动、进程退出后 10 秒自恢复。

### 2.2 尚未完成或不能误报为完成

- 十个平台并未全部形成有效实时覆盖。当前登记表共有 83 个来源，其中 25 个为 `allowed + enabled`；启用来源覆盖 Amazon、TEMU、SHEIN、AliExpress、eBay、Coupang、Joybuy 七个平台。Shopee、Ozon、TikTok Shop 仍没有启用生产来源；“登记了候选 URL”不等于“已经可生产抓取”。
- 当前日报中不少内容是 `feed_summary`。摘要可以做导读，但不会被当作完整原文生成确定性风险结论。
- 2026-08-18 已新增 9 个 `full_text` 来源：雨果 TEMU、AMZ123 TEMU、PDD Holdings 新闻发布、SHEIN Marketplace Corporate、雨果 SHEIN、雨果 AliExpress、雨果 AliExpress 官方发布、AMZ123 AliExpress、Alibaba Group News。项目内逐源探测均为 `success`，健康状态均为 `healthy`。
- 上述 9 个来源当前由 Firecrawl 在入口页生成一份可持久化 Markdown 文档；当本机原生 HTTP 因 `destination_not_public` 被安全策略拒绝时，当前版本尚不会沿入口页链接逐篇执行 Firecrawl 二次抓取。因此“已标记 full_text”表示入口页抓取内容可进入 AI，并不等于每篇文章正文都已独立入库。逐篇二次抓取仍是下一阶段增强项。
- 亿邦动力 TEMU、SHEIN、AliExpress 三个频道入口页可抓，但文章详情页连续两次触发 `haplat` 异常行为拦截，继续保持 `pending_review + disabled + metadata_only`。
- Reuters、Marketplace Pulse 及其他未完成验收的媒体仍保持禁用或待审核；不能因为 Firecrawl 能打开页面就推断已取得转载或长期保存授权。
- 没有 FastAPI/REST 接口。早期 ADR 提到 FastAPI，但当前代码和依赖中并未实现，接手方应以代码现状为准。
- 没有 Alembic 数据库迁移；当前启动时使用 SQLAlchemy `create_all` 建表。
- 本地电脑彻底关机时无法执行 09:00 推送；计划任务只能从睡眠唤醒或在下次开机时补偿。

## 3. 当前运行链路

```text
来源登记 public_sources.yaml
          │
          ▼
合规门禁：只有 allowed + enabled 才运行
          │
          ▼
Firecrawl /v2/scrape 入口页抓取 + RSS/HTML/API/Sitemap 原生采集
          │                         │
          └────原生有文章则优先─────┘
                    │
          原生无文章时使用 Firecrawl 入口页文档
          │
          ▼
提取、语言识别、去重、版本、溯源、快照
          │
          ▼
SQLite：documents / document_versions / analysis_jobs / source_health
          │
          ▼
DeepSeek 结构化分析：事件、影响、风险、置信度、原文依据、建议动作
          │
          ▼
风险策略 + 证据门槛 + 日报编排
          │
          ▼
delivery_outbox（幂等、失败/重试/跳过）
          │
          ▼
飞书卡片或文本降级
```

日报日期使用 `Asia/Shanghai`。`2026-08-18` 的日报窗口是 2026-08-17 09:00（含）至 2026-08-18 09:00（不含）。

## 4. 代码与数据所有权地图

| 位置 | 职责 | 集成注意事项 |
|---|---|---|
| `src/commerce_agent/runtime.py` | 组合根、资源生命周期、调度器与飞书连接 | `_build_*` 是私有函数，不应成为目标项目公共依赖 |
| `src/commerce_agent/config.py` | 环境变量模型与生产配置校验 | 目标项目需要映射到自己的配置/密钥系统 |
| `src/commerce_agent/application.py` | 飞书命令与 BotService | 可复用协议，但不要把飞书消息对象泄漏到业务层 |
| `src/commerce_agent/ingestion/` | 来源注册、合规、采集、提取、去重、快照 | 采集任务必须只有一个调度 owner |
| `src/commerce_agent/integrations/firecrawl.py` | Firecrawl v2 客户端、节流与重试 | 密钥只从环境读取；不能输出请求头 |
| `src/commerce_agent/ingestion/collectors/firecrawl.py` | Firecrawl 优先、原生采集回退 | 只包裹已通过合规门禁的来源 |
| `src/commerce_agent/intelligence/` | 分析、证据、风险、检索、日报、投递 | 依赖持久化状态和群风险策略 |
| `src/commerce_agent/integrations/deepseek.py` | DeepSeek 结构化输出 | 当前模型为 `deepseek-v4-pro`，使用非思考模式 |
| `src/commerce_agent/integrations/feishu.py` | 飞书消息适配 | 目标系统若已有飞书入口，应避免建立第二条重复长连接 |
| `src/commerce_agent/persistence/` | SQLAlchemy 表与仓储 | 当前没有版本化迁移机制 |
| `src/commerce_agent/sources/public_sources.yaml` | 公开来源登记表 | 所有新增来源必须经过登记和审核，不可在代码中散落 URL |
| `src/commerce_agent/sources/official_accounts.yaml` | 手工官方公告白名单 | 用于人工提交，不等同于公开网页采集授权 |
| `scripts/` | Windows 计划任务和守护启动 | 仅适用于本地 Windows 部署 |
| `tests/` | 单元、集成、运维和少量联网 smoke 测试 | 合并到目标项目时必须保留 |
| `commerce_agent.db` | 当前 SQLite 运行数据 | 不应提交 Git；迁移时需停机一致性备份 |
| `data/snapshots/` | 抓取响应快照 | 可能含第三方正文；迁移前做版权与保留期检查 |

## 5. 运行环境与依赖

- Python：`>=3.11,<3.13`。
- 基础依赖：SQLAlchemy asyncio、aiosqlite、Pydantic Settings、OpenAI Python SDK、lark-channel-sdk。
- 采集依赖：httpx/httpcore、feedparser、trafilatura、lingua-language-detector、APScheduler、PyYAML。
- 可选浏览器依赖：Playwright；当前生产必须保持关闭。
- 默认数据库：`sqlite+aiosqlite:///./commerce_agent.db`。
- 当前 Windows 计划任务名：`CrossBorderCommerceAgent`。

安装命令：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ingestion]"
```

## 6. 环境变量交接

### 6.1 必填密钥

| 变量 | 用途 | 交接方式 |
|---|---|---|
| `LARK_APP_ID` | 飞书企业应用 ID | 由目标环境管理员重新录入 |
| `LARK_APP_SECRET` | 飞书企业应用密钥 | 只进入密钥管理器或目标环境变量 |
| `DEEPSEEK_API_KEY` | AI 分析与问答 | 建议目标环境新建/轮换，不复制聊天中的值 |
| `BOT_BIND_CODE` | 首次绑定飞书群 | 重新生成高强度随机值 |
| `FIRECRAWL_API_KEY` | Firecrawl 网页抓取 | 由目标环境重新录入并设置额度告警 |

禁止复制或提交当前 `.env`。应从 `.env.example` 创建目标环境配置，并通过目标项目的 Secret Manager、CI Secret 或操作系统环境变量注入。

### 6.2 关键非密钥配置

```dotenv
DATABASE_URL=sqlite+aiosqlite:///./commerce_agent.db
INGESTION_INTERVAL_MINUTES=120
INGESTION_SCHEDULER_ENABLED=true
INGESTION_BROWSER_ENABLED=false
INTELLIGENCE_ANALYSIS_ENABLED=true
INTELLIGENCE_DAILY_REPORT_ENABLED=true
INTELLIGENCE_ALERTS_ENABLED=true
INTELLIGENCE_QA_ENABLED=true
INTELLIGENCE_TIMEZONE=Asia/Shanghai
INTELLIGENCE_DAILY_HOUR=9
INTELLIGENCE_RISK_PROFILE=aggressive
FIRECRAWL_MAX_CONCURRENCY=1
FIRECRAWL_MAX_ATTEMPTS=3
FIRECRAWL_MIN_REQUEST_INTERVAL_SECONDS=6.5
```

上面是交付环境的建议值，不应覆盖目标项目已有配置。若目标项目也有调度器，必须选择一个唯一 owner。

## 7. 数据库与幂等状态

主要表包括：

- `group_bindings`、`group_intelligence_preferences`：当前目标群与三档策略。
- `sources`、`source_platforms`、`source_material_policies`：来源定义和内容使用范围。
- `fetch_runs`、`source_health`、`source_leases`：运行审计、健康和并发租约。
- `documents`、`document_versions`、`document_provenance`：文章、版本与来源证据。
- `analysis_jobs`、`document_analyses`、`analysis_duplicates`：AI 任务、结果和聚类。
- `daily_reports`、`delivery_outbox`：日报快照和飞书投递幂等状态。

迁移策略：

1. 若不需要历史数据，目标环境新建空库并重新绑定飞书群最安全。
2. 若需要保留历史、避免同一天重复发送，必须迁移 `daily_reports` 与 `delivery_outbox`。
3. 复制 SQLite 前先停止 `CrossBorderCommerceAgent`，完成一致性备份后再启动；不要在进程写入时直接复制数据库文件。
4. 若目标项目使用 PostgreSQL，先补 Alembic，再做显式字段映射和抽样校验；不能把 SQLite 文件直接“转换后即上线”。
5. `data/snapshots` 与数据库分开迁移，先确认目标项目的正文保存期限和授权边界。

## 8. 推荐集成方案

### 8.1 方案 A：独立 worker + 目标项目适配层（推荐）

保持当前 `python -m commerce_agent` 独立运行。目标项目只依赖一个稳定接口：

```python
class CommerceIntelligencePort(Protocol):
    async def collect_now(self, source_ids: tuple[str, ...] | None = None) -> dict: ...
    async def preview_daily(self, report_date: date) -> dict: ...
    async def send_daily(self, report_date: date, variant: str = "formal") -> dict: ...
    async def health(self) -> dict: ...
```

这层接口是**建议新增的边界，当前尚未实现为 HTTP API**。第一阶段可以把现有管理员 CLI 封装成内部任务；正式集成建议新增受鉴权的 localhost API 或目标项目任务队列，返回任务 ID，不要让 Web 请求同步等待网页抓取和 LLM。

优点：

- 不影响目标项目的主进程稳定性。
- 可以独立停止抓取或 AI，而不停止目标系统。
- 保留现有飞书长连接、调度、幂等和审计语义。
- 将来迁移云服务器或任务队列时成本最低。

### 8.2 方案 B：作为 Python 包嵌入目标项目

只有在目标项目同为 Python asyncio、能够统一资源生命周期，并愿意共享数据库迁移时采用。

要求：

- 将 `runtime.py` 中私有组合逻辑提取为公开的 `create_commerce_agent()` 工厂。
- 由目标项目统一启动和关闭 HTTP 客户端、数据库引擎、调度器和飞书 channel。
- 只保留一个 APScheduler 实例或明确 job store/ID 命名空间。
- 只保留一条飞书长连接，或由目标项目把规范化 `InboundMessage` 传给 `BotService`。
- 为目标数据库增加版本化迁移。

不建议直接复制若干内部文件并调用私有函数，这会绕过合规门禁、资源关闭和幂等控制。

## 9. 调度与自动推送语义

当前本地调度：

- Windows 登录时启动一次。
- 每天 08:30 再触发一次并允许唤醒电脑。
- runner 发现 Python 退出后等待 10 秒重启。
- 采集器默认每 120 分钟运行一次，启动时不立即执行普通 interval job。
- 08:40 执行日报预采集、分析和预览。
- 09:00 把已预览日报加入 outbox。
- delivery worker 每分钟发送待发送消息。
- 若程序在 09:00 后首次启动，会执行当日 catch-up；已发送则按幂等规则跳过。

目标项目接入后，Windows 计划任务、目标项目定时器和任何云端 cron 三者只能保留一个主调度入口，否则可能重复采集和重复生成日报。

## 10. Firecrawl 运行约定

当前实现调用 `POST /v2/scrape`，请求 Markdown 主内容。默认：单并发、两次请求起点至少间隔 6.5 秒、最多三次尝试、对 408/429/5xx 退避重试、缓存最大年龄 15 分钟。Firecrawl 返回的 `cacheState`、`cachedAt`、`sourceURL` 和 `statusCode` 用于判断抓取新鲜度与请求结果；HTTP 200 和非空正文只表示抓取成功，不表示文章所述事项仍然有效。

官方接口依据：<https://docs.firecrawl.dev/agent-source-of-truth/python>。接手方升级 API 或 SDK 前应重新核对该页，不要凭旧示例推断参数或返回结构。

关键行为：

- Firecrawl 只处理来源登记表中 `allowed + enabled` 的来源。
- 每次来源运行先尝试一次 Firecrawl 入口页抓取，随后仍执行原生 RSS/HTML/API/Sitemap 采集。
- 原生采集器取得文章时，原生文章是权威输出；原生没有取得文章但 Firecrawl 成功时，才使用 Firecrawl 入口页文档恢复本次采集。
- Firecrawl 失败不会阻止原生采集；原生也失败时才把稳定错误码记录到来源健康。
- 当前没有实现从 Firecrawl 入口页提取链接后逐篇调用 `/scrape`；不要在交接或产品说明中声称已经逐篇抓取。
- Firecrawl 不是合规绕过器：登录页、验证码、明确禁止自动抓取的网页仍须禁用。
- `FIRECRAWL_API_KEY` 缺失时仍可使用原生采集链路。
- 每增加一个启用来源都会消耗调用额度；目标系统需要记录调用次数、429 比例和剩余额度。

## 11. DeepSeek 与报告数据契约

只有达到内容与证据要求的文章才允许进入详细 AI 判断。每条可行动情报至少应保留：

```json
{
  "platform": "temu",
  "headline": "人类可读标题",
  "summary": "一句话通俗解释",
  "impact": "对卖家的实际影响",
  "risk_level": "low|medium|high",
  "evidence_confidence": 0,
  "verification_status": "verified|early_signal",
  "rationale": [{"claim": "判断", "quote": "原文依据"}],
  "actions": [{"action": "建议动作", "owner_type": "负责人类型", "deadline": "日期或未明确"}],
  "uncertainties": ["仍需核实的点"],
  "source_name": "来源名称",
  "source_url": "https://...",
  "content_basis": "full_text|feed_summary|metadata_only"
}
```

接手方不得删除 `source_name`、`source_url`、`content_basis`、`rationale`。截图反馈已经明确：用户需要知道“具体信息从哪里来”，且摘要线索不能伪装成原文分析。

## 12. 用户提供的 TEMU、SHEIN、AliExpress 来源交接清单

状态以 2026-08-18 11:30 本地登记表和真实探测结果为准。“已登记”不代表“已启用”。本轮一次性 Firecrawl 技术验收记录见 `docs/operations/focus-source-firecrawl-probe-2026-08-18.md`。

### 12.1 TEMU

| 来源 | URL | 当前处理状态 | 下一步 |
|---|---|---|---|
| TEMU 卖家中心 | <https://seller.temu.com/seller/login> | 已有同域候选；当前 `denied/disabled` | 只能使用官方授权/API/人工后台通知，不抓登录态 |
| TEMU 官方新闻 | <https://www.temu.com/press.html> | 已有 Press Corner 候选；当前 `denied/disabled` | 复核官方条款后决定，当前不自动抓 |
| 亿邦动力 TEMU | <https://www.ebrun.com/information/cross-Temu> | `pending_review/disabled/metadata_only`；频道页成功、正文被反爬拦截 | 仅保留候选，寻找 RSS/API/授权路径 |
| 雨果跨境 TEMU | <https://m.cifnews.com/pinduoduocrossborder> | `allowed/enabled/full_text`；项目内真实探测 `success` | 保留来源署名和原文链接；补逐篇二次抓取 |
| AMZ123 TEMU | <https://m.amz123.com/temu/news> | `allowed/enabled/full_text`；项目内真实探测 `success` | 保留来源署名和原文链接；补逐篇二次抓取 |
| Marketplace Pulse | <https://www.marketplacepulse.com/> | 已登记；当前 `denied/disabled` | 不直接爬，优先购买授权或只保留人工链接 |
| Reuters | <https://www.reuters.com/> | 已登记零售频道；当前 `authorization_required/disabled` | 使用授权新闻源或合法元数据发现 |
| 美国 CBP 电商政策 | <https://www.cbp.gov/trade/basic-import-export/e-commerce> | 已登记、已启用、真实抓取成功 | 共享官方监管正文，覆盖三个重点平台 |
| 欧盟委员会数字监管 | <https://digital-strategy.ec.europa.eu/en/policies/online-platforms> | 已登记、已启用、真实抓取成功 | 共享官方监管正文，覆盖三个重点平台 |
| PDD Holdings IR | <https://investor.pddholdings.com/news-releases> | `allowed/enabled/full_text`；新闻列表与单篇正文抽查成功 | 必须区分 PDD 集团公告与 TEMU 专属信息；补逐篇二次抓取 |

### 12.2 SHEIN

| 来源 | URL | 当前处理状态 | 下一步 |
|---|---|---|---|
| SHEIN Newsroom | <https://www.sheingroup.com/newsroom> | 已登记；当前 `denied/disabled` | 不自动抓取，等待授权或官方订阅源 |
| SHEIN Corp | <https://www.sheincorp.com/> | `allowed/enabled/full_text`；频道与单篇正文抽查成功 | 官方来源；补逐篇二次抓取 |
| Reuters SHEIN | <https://www.reuters.com/company/shein/> | 已登记；`authorization_required/disabled` | 使用授权源或合法元数据发现 |
| 亿邦动力 SHEIN | <https://www.ebrun.com/information/cross-SHEIN> | `pending_review/disabled/metadata_only`；正文被反爬拦截 | 不依赖自动全文抓取 |
| 雨果跨境 SHEIN | <https://m.cifnews.com/shein> | `allowed/enabled/full_text`；项目内真实探测 `success` | 保留署名和链接；补逐篇二次抓取 |

### 12.3 AliExpress

| 来源 | URL | 当前处理状态 | 下一步 |
|---|---|---|---|
| 速卖通卖家中心 | <https://login.aliexpress.com/seller.htm> | 已有卖家门户候选；当前 `authorization_required/disabled` | 不抓登录态，改用官方通知/API/人工提交 |
| 官方入驻与卖家平台 | <https://sell.aliexpress.com/zh/__pc/newsellerlanding.htm> | 已登记、需授权 | 不抓登录态，寻找官方通知/API |
| 亿邦动力 AliExpress | <https://www.ebrun.com/information/cross-aliexpress> | `pending_review/disabled/metadata_only`；正文被反爬拦截 | 不依赖自动全文抓取 |
| 雨果跨境 AliExpress | <https://m.cifnews.com/aliexpress> | `allowed/enabled/full_text`；项目内真实探测 `success` | 保留署名和链接；补逐篇二次抓取 |
| 雨果官方发布 | <https://m.cifnews.com/aliexpress/platformnews> | `allowed/enabled/full_text`，但仍是 `media` 信任层 | 每篇检查作者/原始来源；不得按频道名直接认定官方 |
| AMZ123 AliExpress | <https://m.amz123.com/aliexpress/news> | `allowed/enabled/full_text`；项目内真实探测 `success` | 保留署名和链接；补逐篇二次抓取 |
| Reuters AliExpress | <https://www.reuters.com/company/aliexpress/> | 已登记；`authorization_required/disabled` | 使用授权源或合法元数据发现 |
| Alibaba Group News | <https://www.alibabagroup.com/news-and-resource> | `allowed/enabled/full_text`；项目内真实探测 `success` | 只保留 AliExpress 相关条目；补逐篇二次抓取 |

建议接入优先级：官方监管/投资者公告 → 官方公开新闻/RSS → 已验收媒体 → 授权媒体 → 媒体元数据发现 → 登录后台人工提交。重点平台排序按用户要求为 TEMU 第一，SHEIN 与 AliExpress 第二批。

## 13. 目标项目落地步骤

### 阶段 0：确认边界

- 确认目标项目路径、技术栈、数据库、部署方式和现有调度器。
- 决定使用方案 A（独立 worker）还是方案 B（进程内嵌入）。默认方案 A。
- 指定飞书长连接唯一 owner、调度唯一 owner、数据库唯一 owner。

### 阶段 1：最小迁移

- 整体保留 `src/commerce_agent`、来源 YAML、必要脚本和测试。
- 目标项目安装当前包或在 monorepo 中作为独立 package/workspace。
- 重新录入密钥，不复制 `.env`。
- 使用空数据库启动，并在测试群重新绑定。

### 阶段 2：适配接口

- 新增第 8 节的 `CommerceIntelligencePort`。
- 将采集和 LLM 调用作为后台任务，接口只返回任务 ID/结果摘要。
- 增加调用方鉴权、超时、幂等键、审计人和触发来源。
- 暴露只读健康状态，不暴露密钥、Cookie、完整模型输入或群 ID。

### 阶段 3：数据与调度切换

- 若迁移历史库，停旧进程后做一致性备份和校验。
- 在新环境先关闭正式日报，只运行采集和预览。
- 影子运行至少一个日报窗口，比较选中条目、原文链接、风险和飞书渲染。
- 关闭旧调度器后再开启新调度器，避免双发。

### 阶段 4：来源扩展

- CBP、欧盟委员会、PDD Holdings、SHEIN Marketplace Corporate 和 Alibaba Group News 已接入；迁移时应保留当前来源 ID、内容范围和署名策略。
- 下一项优先增强是受控逐篇二次抓取：从频道入口提取最新文章链接，同域/路径白名单校验后按额度上限逐篇调用 Firecrawl `/scrape`，每篇独立入库和分析；失败时回退频道页，不绕过验证码或反爬。
- 亿邦动力三频道继续只作为禁用候选，不得因为入口页可抓就启用正文采集。
- 每新增一个来源都补登记测试、离线 fixture、live smoke 记录、来源健康和报告溯源验收。

## 14. 验收命令

```powershell
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli sources list
.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli run --all
.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli health
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli health
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli report preview --date YYYY-MM-DD
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli report test-send --date YYYY-MM-DD --confirm
```

正式切换的通过标准：

- 全量离线测试和 Ruff 通过。
- 目标测试群能绑定、状态查询和 AI 测试。
- 至少一个启用来源真实采集成功，并能追溯到原文 URL。
- 报告里的 `full_text`、`feed_summary`、`metadata_only` 展示正确。
- 测试日报飞书卡片无乱码，原文链接可点击，风险/置信度/依据/建议齐全。
- 相同日报日期的正式发送具有幂等性；更正版使用独立 variant。
- 09:00 调度 owner 唯一，旧环境已经停发。
- 关停/重启后没有丢失 outbox，错过 09:00 能补偿。

## 15. 当前验证状态与已知风险

截至 2026-08-18：

- Windows 计划任务 `CrossBorderCommerceAgent` 正在运行，下次触发为 2026-08-19 08:30。
- 计划任务最近一次启动时间为 2026-08-18 11:26:44，`LastTaskResult=267009` 表示任务实例仍在运行。
- 今日全量采集已运行。部分本机直连因 `destination_not_public` 被安全策略拒绝，但 Firecrawl 回退成功，启用来源整体完成。
- 来源登记表当前为 83 个来源、25 个启用来源。新增 9 个重点来源的项目内逐源探测均为 `success`，每个来源创建 1 份可持久化文档，健康状态均为 `healthy`。
- 最近一次完整离线验收为 `921 passed, 1 skipped`，Ruff 为 `All checks passed`。
- 今日正式日报已存在；随后通过 `resend --confirm` 成功补发更正版，结果为 `sent=1, failed=0, skipped=0`。
- 当前 intelligence health 为 `partial`，存在 22 个历史分析失败记录；不能把它解读为飞书投递失败。
- 当前 intelligence health 明细为 `analysis_failed=22`、`analysis_pending=25`、`analysis_retry_wait=1`、`outbox_failed=0`、`outbox_pending=0`、`risk_profile=aggressive`。
- 新增来源已能以频道页 Firecrawl Markdown 进入 AI 队列，但逐篇正文二次抓取尚未实现；接手方不能把当前每源 1 份入口文档描述成“所有文章均已抓取”。
- `README.md` 中“3/10、3/20”是旧里程碑描述，与当前登记表不完全一致，集成时应以 `sources list`、来源登记表和实际成功记录为准。
- 当前没有数据库迁移工具、HTTP API、集中式指标面板和 Firecrawl 额度监控，这四项是接入更大项目后的优先技术债。

## 16. 回滚方案

1. 关闭目标项目中新启用的采集、分析、日报和预警开关。
2. 停止目标项目的调度 owner，保留数据库和 outbox 审计，不删除记录。
3. 若旧本地 worker 仍保留，确认目标环境已停止后再恢复旧任务，避免双发。
4. 恢复旧数据库备份或重新绑定测试群；不要修改已经 sent 的 outbox 行。
5. 来源问题优先单独设置 `enabled: false`，不需要停掉整个机器人。

## 17. 接手方仍需提供的信息

在真正执行集成前，目标项目负责人需提供：

- 目标仓库的本地绝对路径或 Git 地址。
- 技术栈与 Python/Node/Java 版本。
- 数据库类型和是否保留本项目历史数据。
- 部署环境：本机 Windows、Docker、云服务器或其他。
- 目标项目是否已有飞书机器人、任务队列和定时器。
- 需要嵌入的产品入口：后台页面、API、命令、工作流或消息机器人。
- 正式日报群是否沿用当前绑定，还是重新绑定新群。

拿到这些信息后，接手方可以依据本文件先完成阶段 0；不应在边界未确定时直接复制代码或数据库。
