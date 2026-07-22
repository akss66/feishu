# 权威媒体发现补充规格（2026-07-22）

## 1. 目标

在不直接抓取媒体正文、不绕过付费墙和访问控制的前提下，启用现有 GDELT
媒体发现入口，为 Amazon、TEMU、SHEIN、AliExpress、Shopee、eBay、Coupang、
Ozon、Joybuy 和 TikTok Shop 补充全球权威媒体及跨境行业媒体线索。

本阶段只解决“发现哪些媒体报道了什么”。媒体报道不会自动升级为平台已确认政策，
也不会改变现有官方来源的证据优先级。

## 2. 方案选择

采用“GDELT 元数据发现 + 本地出版机构目录”方案：

- GDELT DOC 2.0 API 只返回文章标题、原始链接、来源域名、发现时间、语言和来源国家；
  机器人不会继续请求原始文章 URL。
- 本地出版机构目录决定哪些域名可进入机器人，并为其提供中文名称和媒体类别。
- 未进入目录的转载站、内容农场和未知域名直接丢弃，不进入 AI 分析和日报。
- 未来获得媒体正式 API、RSS 或商业授权后，再把对应媒体注册为独立来源；同一出版机构
  的 GDELT 结果与直接来源仍只算一个独立证据。

没有采用以下方案：

- 直接抓取媒体网页：容易触及付费墙、版权和自动访问限制。
- 只靠 GDELT 的来源字段、不做白名单：会混入低质量转载站，无法满足“权威媒体”要求。
- 当前立即购买所有商业媒体 API：落地慢、成本高，适合作为后续增强。

GDELT 官方文档说明 DOC 2.0 支持 JSON、来源域名和精确域名查询；GDELT 也明确建议
对媒体分类需求使用外部域名目录。本项目因此在本地做最终域名校验和分类，而不是把
GDELT 当作媒体权威性的判定者。

## 3. 出版机构目录

第一批目录分为三类。类别用于日报标签、排序和 AI 提示，不直接提高事实置信度。

### 3.1 全球权威媒体

- Reuters：`reuters.com`
- Associated Press：`apnews.com`
- Bloomberg：`bloomberg.com`
- Financial Times：`ft.com`
- CNBC：`cnbc.com`
- BBC：`bbc.com`、`bbc.co.uk`

### 3.2 电商与零售专业媒体

- Retail Dive：`retaildive.com`
- Digital Commerce 360：`digitalcommerce360.com`
- EcommerceBytes：`ecommercebytes.com`
- Modern Retail：`modernretail.co`
- Marketplace Pulse：`marketplacepulse.com`

目录只允许 GDELT 返回这些媒体的元数据；它不解除任何媒体对直接抓取、转载、保存全文
或商业使用的限制。例如 AP 全文和 The Guardian 商业/AI 使用仍需单独授权。

### 3.3 中文跨境行业媒体

- 雨果跨境：`cifnews.com`
- 亿邦动力：`ebrun.com`
- 白鲸出海：`baijing.cn`
- 36氪：`36kr.com`

中文行业媒体定位为行业线索来源。涉及政策生效、处罚、费用、封号或平台规则变化时，
必须等待官方来源或第二个独立出版机构交叉确认。

## 4. 数据流与边界

```text
GDELT DOC API（一次、低频、最多 50 条）
  -> 验证 HTTPS 原文 URL 与 domain 一致
  -> 本地媒体目录白名单过滤
  -> 保存标题、时间、原文链接、publisher_key、媒体类别
  -> 平台关键词归因与去重
  -> AI 通俗解释、影响判断、建议动作
  -> 日报标注“媒体报道/待官方确认”
```

约束如下：

- `media-gdelt-cross-border` 保持 `content_scope=metadata_only`。
- 不保存媒体网页正文、图片、视频、Cookie、登录态或付费内容。
- 不请求 GDELT 返回的文章 URL；用户点击原文时由用户自己的浏览器访问。
- 仅允许 HTTPS，继续执行现有 SSRF、DNS、响应体大小、超时、限速和熔断策略。
- 单次最多 50 条，每 120 分钟一次；连续失败三次自动暂停。
- 新增媒体必须通过代码审查或配置校验，不能由外部响应动态提升为权威媒体。

## 5. 证据、置信度与日报展示

- 单一媒体出版机构支撑的事件，证据分数最高 70，只能标记为“待核验”。
- 两个不同 `publisher_key` 报道同一事件时，可以解除单媒体上限，但仍不等于官方确认。
- GDELT 与同一媒体的 RSS/API 是同一个出版机构，不能重复加分。
- 平台官方公告、监管机构文件优先于媒体报道。
- 元数据只有标题时，AI 只能解释标题明确表达的内容，不得补写标题中不存在的事实。
- 日报显示媒体中文名称、类别、原文链接、判断依据、建议动作和不确定性。
- 涉及平台政策时使用“媒体报道”“可能影响”“等待官方确认”等明确措辞。

## 6. 实现边界

本阶段实现：

1. 增加不可由外部数据修改的媒体目录及查询接口。
2. GDELT 适配器只接受目录中的出版机构域名。
3. 在材料元数据/分析上下文中派生媒体名称与类别，不新增数据库迁移。
4. 日报来源名称优先显示目录中的媒体名称，并保留 `publisher_key` 供审计。
5. 通过真实 GDELT 冒烟后才把来源改为 `enabled: true`；若真实结果为空、格式变化或
   网络失败，保持禁用并记录原因。

本阶段不实现：

- 商业媒体 API 采购、账号注册或付费授权。
- 媒体全文抓取、全文存储、图片/视频处理。
- 自动把未知媒体加入白名单。
- 因媒体“级别高”而突破单媒体 70 分上限。

## 7. 测试与验收

先写失败测试，再实现生产代码，至少覆盖：

1. 目录中的域名可解析出稳定媒体名称和类别；大小写、`www.` 与子域名规范化正确。
2. 相似恶意域名（例如 `reuters.com.example`）不匹配。
3. GDELT 固定样本中，白名单媒体被保留，未知媒体被丢弃。
4. 保留条目仍满足 domain 与文章 URL 主机一致的现有安全校验。
5. 媒体名称与类别进入 AI 上下文和日报展示，但证据分数仍受单媒体 70 分上限约束。
6. 来源登记表保持 `metadata_only`、最多 50 条、120 分钟，并在真实冒烟成功后启用。
7. 全量 pytest、Ruff 和 `git diff --check` 通过。
8. 真实冒烟只能请求 GDELT API；日志证明未请求任何媒体原文 URL。
9. 重启后只有一个机器人进程，采集与智能调度器正常运行。

## 8. 官方依据

- GDELT DOC 2.0 API：<https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/>
- GDELT 外部媒体目录方法：<https://blog.gdeltproject.org/using-web-ngrams-3-0-custom-media-catalogs-to-segment-by-country-state-ownership-partisanship-or-other-attributes/>
- AP 内容授权：<https://www.ap.org/content/>
- AP 使用条款：<https://www.ap.org/terms-and-conditions/>
- The Guardian 商业 API/AI 使用授权示例：<https://open-platform.theguardian.com/access/>
- BBC Feed 入口与条款说明：<https://support.bbc.co.uk/platform/feeds/NewsFeeds.htm>
