# 十平台来源验收登记

本文件记录“候选已登记”和“可自动采集”之间的证据边界。`not granted`、`not reviewed`
或失败的冒烟结果均不得解释为采集许可；最终状态以来源注册表为准。

## amazon-about-small-business
- Platform: Amazon
- Publisher: About Amazon
- Entry URL: https://www.aboutamazon.com/news/small-business
- Terms evidence: not reviewed
- Robots evidence: not reviewed
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed
- Offline fixture: not captured
- Live smoke date and result: not run
- Final status: pending_review / enabled=false

## temu-press-corner
- Platform: TEMU
- Publisher: Temu
- Entry URL: https://www.temu.com/br-en/press.html
- Terms evidence: Temu terms prohibit automated crawling and scraping
- Robots evidence: not reviewed
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed
- Offline fixture: not captured
- Live smoke date and result: not run
- Final status: denied / enabled=false

## alibaba-group-news
- Platform: AliExpress
- Publisher: Alibaba Group
- Entry URL: https://www.alibabagroup.com/en-US/news-and-resource
- Terms evidence: not reviewed
- Robots evidence: not reviewed
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed
- Offline fixture: not captured
- Live smoke date and result: not run
- Final status: pending_review / enabled=false

## sea-group-news
- Platform: Shopee
- Publisher: Sea Limited
- Entry URL: https://www.sea.com/news
- Terms evidence: not reviewed
- Robots evidence: not reviewed
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed
- Offline fixture: not captured
- Live smoke date and result: not run
- Final status: pending_review / enabled=false

## coupang-korean-newsroom
- Platform: Coupang
- Publisher: Coupang
- Entry URL: https://news.coupang.com/
- Terms evidence: not reviewed
- Robots evidence: not reviewed
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed
- Offline fixture: not captured
- Live smoke date and result: not run
- Final status: pending_review / enabled=false

## ozon-investor-news
- Platform: Ozon
- Publisher: Ozon
- Entry URL: https://ir.ozon.com/news-and-events/news/
- Terms evidence: not reviewed
- Robots evidence: not reviewed
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed
- Offline fixture: not captured
- Live smoke date and result: not run
- Final status: pending_review / enabled=false

## jd-corporate-blog
- Platform: Joybuy
- Publisher: JD.com
- Entry URL: https://jdcorporateblog.com/
- Terms evidence: not reviewed
- Robots evidence: not reviewed
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed
- Offline fixture: not captured
- Live smoke date and result: not run
- Final status: pending_review / enabled=false

## tiktok-newsroom
- Platform: TikTok Shop
- Publisher: TikTok
- Entry URL: https://newsroom.tiktok.com/en-us/
- Terms evidence: not reviewed for automated newsroom retrieval
- Robots evidence: not reviewed
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed
- Offline fixture: not captured
- Live smoke date and result: not run
- Final status: pending_review / enabled=false

## media-gdelt-amazon
- Platform: Amazon
- Publisher: GDELT index; publisher varies per item
- Entry URL: bounded GDELT DOC 2.0 Amazon query in source registry
- Terms evidence: public DOC 2.0 API documentation recorded
- Robots evidence: not reviewed
- Full-text storage permission: not granted; metadata only
- 90-day relevance evidence: not reviewed
- Offline fixture: existing API fixtures only
- Live smoke date and result: 2026-07-22 and 2026-07-27 both returned HTTP 429; the
  2026-07-27 check used one request, zero retries, and stopped the GDELT pass immediately.
- Final status: allowed / enabled=true (metadata discovery only); current availability is
  degraded by upstream rate limiting and the source circuit may suspend scheduled attempts.

## media-gdelt-temu
- Platform: TEMU
- Publisher: GDELT index; publisher varies per item
- Entry URL: bounded GDELT DOC 2.0 TEMU query in source registry
- Terms evidence: public DOC 2.0 API documentation recorded
- Robots evidence: not reviewed
- Full-text storage permission: not granted; metadata only
- 90-day relevance evidence: not reviewed
- Offline fixture: existing API fixtures only
- Live smoke date and result: not run
- Final status: allowed / enabled=true (metadata discovery only)

## media-gdelt-shein
- Platform: SHEIN
- Publisher: GDELT index; publisher varies per item
- Entry URL: bounded GDELT DOC 2.0 SHEIN query in source registry
- Terms evidence: public DOC 2.0 API documentation recorded
- Robots evidence: not reviewed
- Full-text storage permission: not granted; metadata only
- 90-day relevance evidence: not reviewed
- Offline fixture: existing API fixtures only
- Live smoke date and result: not run
- Final status: allowed / enabled=true (metadata discovery only)

## media-gdelt-aliexpress
- Platform: AliExpress
- Publisher: GDELT index; publisher varies per item
- Entry URL: bounded GDELT DOC 2.0 AliExpress query in source registry
- Terms evidence: public DOC 2.0 API documentation recorded
- Robots evidence: not reviewed
- Full-text storage permission: not granted; metadata only
- 90-day relevance evidence: not reviewed
- Offline fixture: existing API fixtures only
- Live smoke date and result: not run
- Final status: allowed / enabled=true (metadata discovery only)

## media-gdelt-shopee
- Platform: Shopee
- Publisher: GDELT index; publisher varies per item
- Entry URL: bounded GDELT DOC 2.0 Shopee query in source registry
- Terms evidence: public DOC 2.0 API documentation recorded
- Robots evidence: not reviewed
- Full-text storage permission: not granted; metadata only
- 90-day relevance evidence: not reviewed
- Offline fixture: existing API fixtures only
- Live smoke date and result: not run
- Final status: allowed / enabled=true (metadata discovery only)

## media-gdelt-ebay
- Platform: eBay
- Publisher: GDELT index; publisher varies per item
- Entry URL: bounded GDELT DOC 2.0 eBay query in source registry
- Terms evidence: public DOC 2.0 API documentation recorded
- Robots evidence: not reviewed
- Full-text storage permission: not granted; metadata only
- 90-day relevance evidence: not reviewed
- Offline fixture: existing API fixtures only
- Live smoke date and result: not run
- Final status: allowed / enabled=true (metadata discovery only)

## media-gdelt-coupang
- Platform: Coupang
- Publisher: GDELT index; publisher varies per item
- Entry URL: bounded GDELT DOC 2.0 Coupang query in source registry
- Terms evidence: public DOC 2.0 API documentation recorded
- Robots evidence: not reviewed
- Full-text storage permission: not granted; metadata only
- 90-day relevance evidence: not reviewed
- Offline fixture: existing API fixtures only
- Live smoke date and result: not run
- Final status: allowed / enabled=true (metadata discovery only)

## media-gdelt-ozon
- Platform: Ozon
- Publisher: GDELT index; publisher varies per item
- Entry URL: bounded GDELT DOC 2.0 Ozon query in source registry
- Terms evidence: public DOC 2.0 API documentation recorded
- Robots evidence: not reviewed
- Full-text storage permission: not granted; metadata only
- 90-day relevance evidence: not reviewed
- Offline fixture: existing API fixtures only
- Live smoke date and result: not run
- Final status: allowed / enabled=true (metadata discovery only)

## media-gdelt-joybuy
- Platform: Joybuy
- Publisher: GDELT index; publisher varies per item
- Entry URL: bounded GDELT DOC 2.0 Joybuy query in source registry
- Terms evidence: public DOC 2.0 API documentation recorded
- Robots evidence: not reviewed
- Full-text storage permission: not granted; metadata only
- 90-day relevance evidence: not reviewed
- Offline fixture: existing API fixtures only
- Live smoke date and result: not run
- Final status: allowed / enabled=true (metadata discovery only)

## media-gdelt-tiktok-shop
- Platform: TikTok Shop
- Publisher: GDELT index; publisher varies per item
- Entry URL: bounded GDELT DOC 2.0 TikTok Shop query in source registry
- Terms evidence: public DOC 2.0 API documentation recorded
- Robots evidence: not reviewed
- Full-text storage permission: not granted; metadata only
- 90-day relevance evidence: not reviewed
- Offline fixture: existing API fixtures only
- Live smoke date and result: not run
- Final status: allowed / enabled=true (metadata discovery only)

## media-cifnews-cross-border
- Platform: 原 10 平台；入口候选标题和详情正文必须命中同一套受控平台别名，单次
  live smoke 不等于已验证每个平台的 90 日材料
- Publisher: 雨果跨境
- Entry URL: https://www.cifnews.com/
- Terms evidence: public privacy policy reviewed; registry review only allows low-frequency
  access to this exact public entry and does not authorize login, access-control bypass, or
  full-text republication
- Robots evidence: URL recorded in the registry; no broader crawl permission is inferred
- Full-text storage permission: temporary analysis body only; snapshots and repository body
  are removed or redacted at the seven-day cutoff, while short evidence, hash, attribution,
  and original link may remain
- 90-day relevance evidence: not reviewed per platform
- Offline fixture: captured for scoped candidate filtering and complete-article extraction
- Collection boundary: HTTPS `www.cifnews.com` only, `/article/` paths only, at most 10 detail
  candidates per run, and no more often than every 120 minutes
- Live smoke date and result: 2026-07-27 PASS; see the dated evidence table below
- Final status: allowed / enabled=true / `full_text` / trust tier `media`

## media-ennews-cross-border
- Platform: original 10 platforms; relevance not yet verified per platform
- Publisher: 亿恩网
- Entry URL: https://www.ennews.com/
- Terms evidence: official about page reviewed; authorization not granted
- Robots evidence: interactive verification observed; no bypass attempted
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed per platform
- Offline fixture: not captured
- Live smoke date and result: blocked by interactive verification; no retry
- Final status: authorization_required / enabled=false

## media-chwang-cross-border
- Platform: original 10 platforms; relevance not yet verified per platform
- Publisher: 出海网
- Entry URL: https://www.chwang.com/
- Terms evidence: copyright notice requires permission and some items have third-party origins
- Robots evidence: not reviewed
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed per platform
- Offline fixture: not captured
- Live smoke date and result: not run
- Final status: authorization_required / enabled=false

## media-dsb-cross-border
- Platform: original 10 platforms; relevance not yet verified per platform
- Publisher: 电商报
- Entry URL: https://www.dsb.cn/
- Terms evidence: authorization not granted
- Robots evidence: automated request returned HTTP 403; no bypass attempted
- Full-text storage permission: not granted
- 90-day relevance evidence: not reviewed per platform
- Offline fixture: not captured
- Live smoke date and result: HTTP 403; no retry
- Final status: authorization_required / enabled=false

## media-100ec-cross-border
- Platform: 原 10 平台；入口候选标题和详情正文必须命中同一套受控平台别名，单次
  live smoke 不等于已验证每个平台的 90 日材料
- Publisher: 网经社跨境电商台
- Entry URL: https://imgs-b2b.100ec.cn/list--3--1.html
- Terms evidence: individual report attribution terms do not grant site-wide collection;
  registry review only allows low-frequency access to the exact public static entry and does
  not authorize login, access-control bypass, or full-text republication
- Robots evidence: URL recorded in the registry; no broader crawl permission is inferred
- Full-text storage permission: temporary analysis body only; snapshots and repository body
  are removed or redacted at the seven-day cutoff, while short evidence, hash, attribution,
  and original link may remain
- 90-day relevance evidence: not reviewed per platform
- Offline fixture: captured for scoped candidate filtering, complete-article extraction, and
  JavaScript security-check rejection
- Collection boundary: HTTPS `imgs-b2b.100ec.cn` only, `/detail--` paths only, at most 5 detail
  candidates per run, and no more often than every 120 minutes
- Live smoke date and result: 2026-07-27 PASS; see the dated evidence table below
- Final status: allowed / enabled=true / `full_text` / trust tier `media`

## GDELT 可抓取原文发布者

以下发布者具有明确的公开再利用依据，并且只允许访问列出的精确主机。它们不是十个平台
自身的官方公告来源；在日报中应归为“监管机构/公共机构信息”。

### ftc.gov

- Publisher: United States Federal Trade Commission
- Allowed hosts: `ftc.gov`
- Terms evidence: https://www.ftc.gov/policy-notices/website-policy states that most FTC
  material is United States Government work in the public domain and requests attribution.
- Robots evidence: https://www.ftc.gov/robots.txt permits public news paths and declares a
  five-second crawl delay; disallowed paths remain inaccessible.
- Review date: 2026-07-27
- Final status: `allowed_public`; original fetch still requires the runtime switch and article
  quality gate.

### gov.uk

- Publisher: UK Government
- Allowed hosts: `gov.uk`
- Terms evidence: https://www.gov.uk/help/terms-conditions states that most GOV.UK content is
  published under the Open Government Licence and may be reproduced under its conditions.
- Robots evidence: https://www.gov.uk/robots.txt permits ordinary public pages while excluding
  search, print, and explicitly blocked crawler paths.
- Review date: 2026-07-27
- Final status: `allowed_public`; attribution and licence conditions remain mandatory.

### european-union.europa.eu

- Publisher: European Union
- Allowed hosts: `european-union.europa.eu`
- Terms evidence: https://european-union.europa.eu/legal-notice states that EU-owned site
  content is CC BY 4.0 unless otherwise indicated; attribution and change disclosure are
  required, while third-party content is excluded.
- Robots evidence: https://european-union.europa.eu/robots.txt permits ordinary public content
  and excludes administrative, account, search, and other listed paths.
- Review date: 2026-07-27
- Final status: `allowed_public`; pages carrying a different copyright notice must be rejected
  or downgraded to metadata.

## 当前严格覆盖矩阵

“达标”按每个平台至少 2 个独立、可归属、允许使用的有效发布者计算；同一发布者的多语言
URL 只计 1 个来源。摘要和元数据线索不计入有效来源。

| 平台 | 有效来源 / 目标 | 当前结论 |
| --- | ---: | --- |
| Amazon | 0 / 2 | 仅有摘要型 feed，不计为全文有效来源 |
| TEMU | 0 / 2 | 尚无通过验收的全文来源 |
| SHEIN | 0 / 2 | 尚无通过验收的全文来源 |
| AliExpress | 0 / 2 | 尚无通过验收的全文来源 |
| Shopee | 0 / 2 | 尚无通过验收的全文来源 |
| eBay | 1 / 2 | eBay Press Room |
| Coupang | 1 / 2 | Seller University |
| Ozon | 0 / 2 | 尚无通过验收的全文来源 |
| Joybuy | 1 / 2 | 多语言 URL 属于同一发布者，只计 1 个 |
| TikTok Shop | 0 / 2 | 尚无通过验收的全文来源 |

当前严格覆盖为 **3 / 10 个平台、3 / 20 个有效来源**。机器人可以在部分覆盖状态下运行和
生成日报，但必须在卡片中明确展示覆盖范围与异常，不得声称已完成十平台全覆盖。

雨果跨境和网经社已经是可运行的直连媒体来源，但本次 live smoke 只证明每个发布者各有
1 篇当前材料通过正文门并到达提取层；它没有证明两个综合媒体在最近 90 日分别为全部十个平台
提供合格内容，也没有提供最近三次计划采集的稳定性证据。因此本节的严格 `2/2` 计数暂不增加。

## 离线验收边界

- 全量单元、集成和契约测试以及 Ruff 检查必须通过。
- 公网 smoke 默认跳过，只有明确批准真实网络验证时才启用。
- 10 个 GDELT 发现源已启用，但仍是元数据线索；只有受控取得并通过质量门的原文才进入
  LLM 分析。
- 未配置的授权媒体数据商使用禁用适配器，返回空结果，不产生网络请求。

## 直连媒体的运行边界

两个直连来源都使用现有 HTTP HTML collector，不使用生产浏览器。每次运行先请求登记的入口页，
然后才对同时满足 HTTPS、精确 host、路径前缀和平台相关标题的候选执行受控详情正文请求。详情
响应还必须通过正文门；不满足条件的页面不会进入提取或 LLM。

正文门的稳定拒绝码为：

- `article_media_type_rejected`
- `article_access_wall`
- `article_rights_restricted`
- `article_body_incomplete`
- `article_platform_irrelevant`

详情 HTTP 或 URL 安全失败会收敛为受控失败码（例如 `detail_fetch_failed`），记录该候选失败后
继续处理同一来源的其他候选。来源运行本身具有独立 lease、结果和熔断状态；批量运行并发调度
各来源，所以一个来源的正常失败摘要不会阻止其他来源完成。验证码、登录、会员、付费墙、
JavaScript 安全检查、403 或耗尽重试后的 429 均只允许安全失败，不得重试身份、使用代理、
改变 host/path、借用浏览器状态或降低正文门。

`TrustTier.MEDIA + full_text` 的直连媒体与 GDELT 临时媒体正文使用同一个固定七天清理边界。
每次来源运行前，系统按 `started_at - 7 days` 清理该来源旧 snapshot，并在仓库中脱敏到期正文。
飞书只接收分析、短证据和原文链接，不发送或长期保存整篇媒体正文。

GDELT 是独立、可选的 `metadata_only` 发现层，不是两个直连来源的上游依赖。
GDELT Amazon 在 2026-07-27 的单次请求返回 429，只会使对应 GDELT 来源进入
`rate_limited`/熔断流程；不会关闭或跳过雨果跨境、网经社，也不会阻止 09:00 日报按已有材料
生成。

## 2026-07-27 直连媒体 live smoke 验收

本表来自 Task 5 唯一一次带 `RUN_CHINESE_MEDIA_SMOKE=1` 的受控运行。测试使用生产 registry，
只在副本上把 `item_limit` 收紧为 1；每个来源最多两次 GET、单并发、每域 0.5 RPS、
20 秒超时、零重试、零重定向和 2 MB 单响应上限。

| 来源 | 生产入口 URL | HTTP 结果 | 请求数 | 进入详情抓取的候选数 | 提取文档数 | 正文门 | 稳定失败码 |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| `media-cifnews-cross-border` | `https://www.cifnews.com/` | 入口与详情均为 HTTP client 接受的 2xx；精确状态码未持久化 | 2 | 1 | 1 | 接受；提取结果为 `full_text`，publisher 为 `cifnews.com` | 不适用 |
| `media-100ec-cross-border` | `https://imgs-b2b.100ec.cn/list--3--1.html` | 入口与详情均为 HTTP client 接受的 2xx；精确状态码未持久化 | 2 | 1 | 1 | 接受；提取结果为 `full_text`，publisher 为 `100ec.cn` | 不适用 |

quiet pytest 输出没有留存具体详情文章 URL 或精确执行时刻，因此这里不补写这些字段。该次运行
未登录、未处理或破解验证码，未使用 Cookie、Authorization、代理、替代身份、浏览器状态、
重试、重定向或自定义伪装 header；也未遇到或绕过 403、429、challenge 或其他访问控制。
GDELT 没有参与这两个直连来源的 smoke，其既有 429 不影响以上 PASS。

后续手工验收从项目根目录使用以下命令；先运行单一来源并检查 `health`，再分析一篇待处理材料。
`report preview` 不发送消息，`report send` 只允许在预览核对、绑定测试群和人工确认后执行：

```powershell
.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli run --source media-cifnews-cross-border
.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli health
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli analyze --pending --limit 1
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli report preview --date 2026-07-27
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli report send --date 2026-07-27 --confirm
```

## 2026-07-27 GDELT 上游受控网络 smoke 结论

- Amazon SP-API RSS 和 eBay RSS 在本轮白名单 smoke 中先完成请求。
- 第一条 GDELT Amazon 查询返回 HTTP 429。
- GDELT 请求关闭重试、关闭重定向且只执行一次；未更换身份、代理或查询方式。
- 收到 429 后停止剩余 9 个 GDELT 平台探测，未请求任何媒体原文。
- `GDELT_ORIGINAL_FETCH_ENABLED` 保持 `false`。在 GDELT 发现查询成功且至少一个
  `allowed_public` 原文通过质量门之前，不得打开。
- 10 个 GDELT 来源仍作为配置层的元数据发现入口；运行时必须如实报告 rate-limited，
  连续失败达到现有阈值后由来源熔断器暂停，不影响官方来源和 09:00 日报。
