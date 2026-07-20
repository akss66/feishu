# 公开资讯来源采集与入库设计

- 日期：2026-07-20
- 状态：待用户复核
- 阶段：第二阶段——来源登记、合规采集、去重、版本化与健康监控

## 1. 目标

为跨境电商飞书智能体建立可持续维护的公开资讯底座，覆盖 Amazon、TEMU、SHEIN、AliExpress、Shopee、eBay、Coupang、Ozon、Joybuy、TikTok Shop 的全球站点，并补充权威行业媒体。

系统每两小时自动执行一次采集，也允许管理员通过命令行手动执行。每条内容保留原文、来源、语言、发布时间、抓取时间和版本历史，为下一阶段的翻译、AI 提炼、日报和紧急预警提供可靠输入。

所有采集必须同时满足公开可访问、来源条款允许和访问控制允许三个条件。不得绕过登录、验证码、付费墙、访问限制或平台授权要求。

## 2. 已确认的产品决策

- 10 个指定平台全部进入来源登记表，不因暂时无法公开采集而从覆盖范围中消失。
- 来源同时包括平台官方公开来源和权威行业媒体。
- 优先使用 RSS/Atom、公开 API、Sitemap；其次使用合规的公开 HTML 页面。
- 只有确有必要且页面公开允许时，才启用浏览器渲染采集器。
- 本阶段保存原文并识别语言，不执行翻译、AI 摘要、日报编排或飞书推送。
- 自动采集间隔为 120 分钟，手动采集和自动采集调用同一个业务服务。
- 本地 MVP 使用 SQLite；原始快照写入 Git 忽略的本地目录。
- AliExpress 等禁止未经授权系统化抓取的来源标记为 `authorization_required`，只接入明确允许的公开 feed、API 或已获授权来源。

## 3. 范围

### 3.1 本阶段包含

- 配置驱动的来源登记表和平台映射。
- RSS/Atom、Sitemap、HTML、公开 JSON API 四类通用采集器。
- 可选的公开页面浏览器渲染采集器。
- 合规状态检查、robots 检查和目标 URL 安全校验。
- 内容抽取、语言识别、URL 规范化、内容哈希。
- 文档去重、内容变更版本化、跨来源内容分组。
- 抓取运行记录、来源健康状态、失败隔离和结构化日志。
- 10 个平台的覆盖状态，以及至少 30 个官方或权威媒体种子来源。
- 可重复、无真实网络依赖的自动化测试，以及受控的真实来源冒烟测试。

### 3.2 本阶段不包含

- 机器翻译、DeepSeek 调用和 AI 摘要。
- 日报排版、风险评分、紧急预警判定和飞书推送。
- 登录态、Cookie、账号密码、付费订阅内容或验证码处理。
- 代理池、反爬绕过、访问限制规避或未获授权的系统化采集。
- 语义向量去重；本阶段只做确定性的 URL 和内容哈希去重。
- Web 管理后台和 FastAPI 服务。

## 4. 总体架构

```text
来源登记表
   │
   ├── 合规状态 / 平台映射 / 信任等级 / 采集方式
   ▼
调度器（每 120 分钟）或管理员 CLI
   ▼
IngestionService
   ├── 合规与 URL 安全检查
   ├── 域名限速 / 并发控制 / 重试
   ├── RSS / Sitemap / HTML / API / Browser Collector
   ├── 内容抽取与语言识别
   ├── URL 规范化与 SHA-256 去重
   └── 快照、文档、版本、运行结果和健康状态持久化
```

生产运行时中，采集调度器与现有飞书长连接作为同一进程内的两个服务启动，共享数据库但互不阻塞。某一来源失败不得中断其他来源，也不得使飞书机器人离线。管理员 CLI 独立启动进程，但复用相同的 `IngestionService`。

## 5. 组件与职责

### 5.1 `SourceRegistry`

加载并验证版本控制内的 YAML 来源清单，将其同步到数据库。每个来源必须声明：

- 稳定的 `source_id`、名称和基础 URL；
- `official` 或 `media` 信任等级；
- 覆盖的平台与地区；
- `rss`、`sitemap`、`html`、`api` 或 `browser` 采集方式；
- 合规状态和人工复核依据；
- 是否启用、抓取间隔、解析规则和可选入口 URL。

配置验证失败时拒绝启动采集服务，并明确指出来源 ID 和字段。

### 5.2 `CompliancePolicy`

合规状态为固定枚举：

- `allowed`：已复核，可公开采集；
- `pending_review`：条款或技术条件尚未确认，不执行真实采集；
- `denied`：明确禁止，不执行采集；
- `authorization_required`：只有获得书面授权或官方 API/feed 后才能启用。

启用来源必须为 `allowed`。首次访问前检查 robots 规则；robots 与来源条款任一禁止时都不请求正文。重定向后的 URL 必须重新执行安全和域名检查。

### 5.3 `Collector`

所有采集器实现统一异步接口：

```python
class Collector(Protocol):
    async def collect(
        self,
        source: SourceDefinition,
        context: FetchContext,
    ) -> AsyncIterator[CollectedItem]: ...
```

- `FeedCollector`：解析 RSS/Atom，保留条目链接、标题、摘要和时间。
- `SitemapCollector`：解析 sitemap index/urlset，只访问符合来源规则的新 URL。
- `HtmlCollector`：请求公开列表页与详情页，使用配置选择器发现链接。
- `ApiCollector`：只调用无需秘密凭据且条款允许的公开 JSON API。
- `BrowserCollector`：仅用于允许公开访问但必须执行 JavaScript 的页面，作为可选依赖；不开启持久上下文，不保存 Cookie，不执行登录。

采集器只负责取得候选内容，不直接写数据库。

### 5.4 `ContentExtractor`

将 HTML 或 feed 内容转换为统一的 `ExtractedDocument`：标题、正文、作者、发布时间、原始语言、规范 URL、来源元数据。正文抽取优先使用 Trafilatura，来源级选择器作为可审计的覆盖规则。语言识别必须返回语言代码和置信度；低置信度时记录 `und`，不猜测。

### 5.5 `Deduplicator`

- 规范 URL：协议和主机小写、移除 fragment、删除已知追踪参数、稳定排序查询参数；不随意删除可能影响正文定位的业务参数。
- 同一来源、同一规范 URL、同一内容哈希：幂等跳过。
- 同一来源、同一规范 URL、内容哈希变化：在原文档下新增版本。
- 不同 URL 但规范化正文哈希相同：保留各自来源记录，并用相同 `content_group_hash` 关联。
- 内容哈希使用规范化正文的 SHA-256；原始快照哈希单独记录。

### 5.6 `SnapshotStore`

原始响应按以下路径压缩保存：

```text
data/snapshots/YYYY/MM/DD/<source-id>/<sha256>.bin.gz
```

数据库只保存相对路径、哈希、媒体类型、字节数和获取时间。快照目录必须被 Git 忽略，不保存 Cookie、Authorization 头、访问令牌或请求查询串。

### 5.7 `IngestionService`

统一编排单来源或全部来源的运行：加锁、合规检查、限流、采集、抽取、去重、事务写入、运行统计和健康状态更新。同一来源不允许重叠运行；管理员手动触发时若已有运行，返回“正在运行”而不是再开一个任务。

### 5.8 `Scheduler` 与 `SourceHealthService`

APScheduler 的 asyncio 调度器每 120 分钟调用一次全量采集，并以配置的时区记录计划时间。服务启动后不立即制造并发重复抓取；首次执行时间由固定调度规则决定，管理员可用 CLI 立即执行。

健康服务根据最近运行时间、连续失败次数、最后成功时间和抓取结果生成平台覆盖状态：

- `official_public_covered`
- `public_covered_seller_center_pending`
- `partial`
- `error`
- `unconnected`

## 6. 数据模型

### `sources`

保存来源定义：`id`、名称、入口 URL、信任等级、采集类型、合规状态、启用状态、地区、语言提示、配置 JSON、条款依据 URL、复核日期、创建/更新时间。

### `source_platforms`

来源与平台的多对多映射。媒体来源可映射多个平台；全球综合媒体也可使用 `global` 范围。

### `fetch_runs`

每次来源运行一条记录：计划/手动触发方式、开始/结束时间、状态、HTTP 统计、发现/新增/更新/跳过/失败数量、分类错误代码和截断后的安全错误摘要。

### `documents`

稳定文档身份：来源 ID、规范 URL、首次/最近发现时间、当前版本 ID、内容分组哈希。唯一约束为 `(source_id, canonical_url)`。

### `document_versions`

不可变版本：文档 ID、标题、正文、语言代码和置信度、作者、原始发布时间、内容哈希、快照路径、HTTP ETag/Last-Modified、获取时间。唯一约束为 `(document_id, content_hash)`。

### `source_health`

每个来源一条汇总：最近尝试、最近成功、连续失败次数、最近错误分类、下一次计划时间和计算后的健康状态。

写入文档、版本、运行统计与健康状态时使用短事务；网络请求和正文抽取不得占用数据库事务。

## 7. 来源种子策略

首版来源清单至少包含 30 个条目，并保证每个平台至少存在一条官方来源或一条明确的待授权记录。候选入口包括：

| 平台/类型 | 首批候选入口 | 初始处理 |
|---|---|---|
| Amazon | Seller Forums、公开卖家新闻与政策入口 | 逐站点复核后启用 |
| TEMU | Seller Center 公开入口 | 公开页可用，登录内容不采集 |
| SHEIN | SHEIN Group Newsroom、公开 Marketplace 新闻 | 复核后启用 |
| AliExpress | 官方条款、明确允许的公开 feed/API | 未授权系统化页面采集标为 `authorization_required` |
| Shopee | 各地区 Seller Education/公告候选页 | 逐地区验证，未确认先 `pending_review` |
| eBay | Seller News、公开政策中心 | 复核后启用 |
| Coupang | Global Sellers Rules and Policies | 复核后启用 |
| Ozon | Seller Media/News 候选入口 | 解决跳转并复核后启用 |
| Joybuy | Joybuy Newsroom | 复核后启用 |
| TikTok Shop | TikTok Shop Academy、Policy Pulse | 逐地区复核后启用 |
| 行业媒体 | Marketplace Pulse、EcommerceBytes、Digital Commerce 360、Reuters | 仅采集条款允许的公开列表/feed |

`official` 与 `media` 必须在数据中明确区分。媒体报道不自动视为平台政策；下一阶段生成内容时必须能标注“媒体报道、官方未确认”。

## 8. 网络、并发与错误策略

- 全局最大并发：4。
- 每域名速率：不高于每秒 1 个请求。
- 单请求超时：20 秒。
- 单响应上限：10 MiB，流式读取，超限立即停止。
- 对 429、连接瞬断和 5xx 最多重试 3 次，指数退避并尊重 `Retry-After`。
- 普通 4xx 不重试；401/403 触发合规复核并停用该次采集。
- 使用 ETag 和 Last-Modified 条件请求，304 计为成功且不产生新版本。
- 每个来源独立运行和记录失败，单一来源错误不取消批次中的其他来源。
- 应用退出时停止接收新任务，给予在途写入有限的完成时间，再安全关闭调度器和数据库。

## 9. 安全与隐私边界

- 只允许来源登记表中的 URL；协议只允许 HTTP/HTTPS。
- DNS 解析和每次重定向都拒绝 localhost、私网、链路本地、元数据地址和非 HTTP 协议，防止 SSRF。
- 不携带浏览器或用户 Cookie，不接受来源配置中的任意请求头或秘密字段。
- User-Agent 使用明确的应用标识；不伪装真实浏览器身份来绕过限制。
- 日志只记录来源 ID、去除查询参数的主机/路径、状态码、时长、字节数和分类错误，不记录响应正文、Cookie、令牌或完整查询串。
- 快照目录和运行日志均不进入 Git；数据库和备份按运行环境权限保护。

## 10. 配置与依赖

新增环境变量及默认值：

```dotenv
INGESTION_INTERVAL_MINUTES=120
INGESTION_GLOBAL_CONCURRENCY=4
INGESTION_DOMAIN_RPS=1
INGESTION_HTTP_TIMEOUT_SECONDS=20
INGESTION_MAX_RESPONSE_BYTES=10485760
INGESTION_BROWSER_ENABLED=false
SNAPSHOT_DIR=./data/snapshots
INGESTION_USER_AGENT=CrossBorderCommerceAgent/0.1
```

新增可选依赖组：

- `ingestion`：`httpx>=0.28.1,<1`、`feedparser>=6.0.12,<7`、`trafilatura>=2.1,<3`、`APScheduler>=3.11.3,<4`，以及轻量语言识别依赖；具体语言库在实施计划中以体积、许可证和离线能力验证后固定。
- `browser`：`playwright>=1.61,<2`，只有需要浏览器渲染的已允许来源才安装。

## 11. 命令接口

安装常规采集能力：

```powershell
python -m pip install -e ".[dev,ingestion]"
```

可选安装浏览器采集能力：

```powershell
python -m pip install -e ".[dev,ingestion,browser]"
python -m playwright install chromium
```

运行现有飞书机器人和自动采集调度器：

```powershell
python -m commerce_agent
```

管理员命令：

```powershell
python -m commerce_agent.ingestion_cli sources list
python -m commerce_agent.ingestion_cli run --all
python -m commerce_agent.ingestion_cli run --source <source-id>
python -m commerce_agent.ingestion_cli health
```

`run` 成功时退出码为 0；参数/配置错误为 2；采集已执行但有来源失败为 3。输出不得包含秘密、Cookie 或带查询参数的完整 URL。

## 12. 项目结构

```text
src/commerce_agent/
├── config.py
├── runtime.py
├── ingestion_cli.py
├── ingestion/
│   ├── models.py
│   ├── registry.py
│   ├── compliance.py
│   ├── service.py
│   ├── scheduler.py
│   ├── extract.py
│   ├── dedupe.py
│   ├── snapshots.py
│   ├── security.py
│   └── collectors/
│       ├── base.py
│       ├── feed.py
│       ├── sitemap.py
│       ├── html.py
│       ├── api.py
│       └── browser.py
├── persistence/
│   ├── models.py
│   └── repositories.py
└── sources/
    └── public_sources.yaml
tests/
├── fixtures/ingestion/
├── test_source_registry.py
├── test_ingestion_security.py
├── test_collectors.py
├── test_deduplication.py
├── test_ingestion_service.py
├── test_ingestion_scheduler.py
└── test_ingestion_cli.py
```

来源规则使用数据配置；只有通用采集器无法清晰表达且来源长期稳定时，才增加小型平台专用适配器。

## 13. 代码风格

- 延续现有 Python 3.11、类型注解、asyncio、SQLAlchemy 2.x 和 Pydantic Settings 风格。
- 网络、数据库、文件存储通过小接口注入，测试不访问真实网络。
- 错误使用稳定分类码，不用字符串匹配驱动控制流。
- 业务服务不直接 `print`；CLI 负责展示，运行时使用结构化日志。
- 来源定义和数据库模型采用明确枚举，禁止散落的魔法字符串。

真实接口示例：

```python
async def ingest_source(self, source_id: str, trigger: Trigger) -> RunSummary:
    source = self.registry.require_enabled(source_id)
    self.compliance.require_allowed(source)
    async with self.source_locks.acquire(source.id):
        return await self._collect_and_persist(source, trigger)
```

## 14. 测试策略

### 单元测试

- YAML 来源清单校验、枚举和平台覆盖计算。
- URL 规范化、追踪参数删除、重定向安全检查和私网地址拒绝。
- robots/合规状态阻断行为。
- RSS、Sitemap、HTML、API fixture 解析与正文抽取。
- 内容哈希、幂等跳过、版本新增和跨来源内容分组。
- 重试分类、响应大小限制、日志脱敏和退出码。

### 集成测试

- 使用临时 SQLite 和临时快照目录运行完整 `IngestionService`。
- 用 `httpx.MockTransport` 或同等测试传输模拟 200、304、429、403、5xx、重定向和超限响应。
- 验证单来源失败不影响其他来源，调度器与 CLI 调用相同服务。
- 验证飞书长连接启动失败或采集失败时，另一服务的生命周期行为符合设计。

### 真实来源冒烟测试

真实网络测试不进入默认测试套件。通过显式环境开关运行，只选择少量已复核的官方公开来源，限制请求数量，并只验证可达性、媒体类型和解析到至少一个候选项。冒烟失败不自动改写合规状态，而是生成复核记录。

### 验证命令

```powershell
python -m pytest
python -m ruff check .
python -m commerce_agent.ingestion_cli sources list
python -m commerce_agent.ingestion_cli run --source <approved-smoke-source-id>
python -m commerce_agent.ingestion_cli health
```

## 15. 验收标准

- 10 个平台均可在 CLI 中看到覆盖状态和至少一个官方来源或待授权条目。
- 种子来源总数不少于 30，官方与媒体来源有清晰区分。
- 全量手动运行和两小时调度使用同一服务，且同一来源不会重叠运行。
- 相同响应重复运行不新增版本；正文变化时只新增一个不可变版本。
- 任意单一来源超时、限流或解析失败不会阻止其他来源完成。
- 每条已入库内容可追溯到来源、规范 URL、原始发布时间、抓取时间、语言、内容哈希和快照。
- `pending_review`、`denied`、`authorization_required` 来源不会发出正文请求。
- 自动化测试不依赖真实网络；所有测试和 Ruff 检查通过。
- 日志、数据库字段和快照元数据中不出现密钥、Cookie、认证头或完整敏感查询串。
- 现有飞书“帮助、状态、绑定本群、AI 测试”能力不回归。

## 16. 上线与回退

1. 先合入数据库表、来源登记和 CLI，但默认不启动调度器。
2. 对少量官方来源执行受控冒烟测试，确认解析、限速和快照。
3. 启用全量 `allowed` 来源并观察至少一个完整的两小时周期。
4. 确认飞书长连接稳定后再默认开启调度器。

回退时关闭采集调度开关即可；现有飞书机器人继续运行。新增数据库表和快照保留，不执行破坏性删除，以便排查和恢复。

## 17. 开放问题

产品范围和关键技术选择均已确认，没有阻塞实施计划的开放问题。语言识别库属于可替换的内部依赖，实施计划将通过许可证、离线运行和安装体积的验证结果固定具体版本，不改变本文对外行为。

## 18. 参考资料

- Amazon Seller Forums: https://sellercentral.amazon.com/seller-forums
- TEMU Seller Center: https://seller.temu.com/
- SHEIN Group Newsroom: https://www.sheingroup.com/newsroom/shein-launches-global-integrated-marketplace
- AliExpress Terms of Use: https://terms.alicdn.com/legal-agreement/terms/suit_bu1_aliexpress/suit_bu1_aliexpress202204182115_66077.html
- eBay Seller News: https://export.ebay.com/en/resources/seller-news/
- eBay Policies: https://export.ebay.com/en/fees-regulations-policies/ebay-policies/
- Coupang Rules and Policies: https://globalsellers.coupang.com/en/rules-and-policies/
- TikTok Shop Academy: https://seller.tiktok.com/blog/tiktok-shop-academy-your-resource-for-policies-and-best-practices/10024085
- TikTok Shop Policy Pulse: https://seller-us.tiktok.com/university/essay?default_language=en&identity=1&knowledge_id=6747273381791534
- Joybuy Newsroom: https://about.joybuy.com/
- Marketplace Pulse: https://www.marketplacepulse.com/
- EcommerceBytes: https://www.ecommercebytes.com/
- Digital Commerce 360 Cross-Border Ecommerce: https://www.digitalcommerce360.com/topic/cross-border-ecommerce/
- HTTPX Async Support: https://www.python-httpx.org/async/
- feedparser documentation: https://feedparser.readthedocs.io/en/latest/introduction.html
- Trafilatura core functions: https://trafilatura.readthedocs.io/en/latest/corefunctions.html
- APScheduler: https://pypi.org/project/APScheduler/
- Playwright for Python: https://playwright.dev/python/docs/intro
