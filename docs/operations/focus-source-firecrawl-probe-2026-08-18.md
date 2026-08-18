# 重点来源 Firecrawl 受控抓取验收（2026-08-18）

本次仅测试匿名公开页面，不登录、不绕过验证码、不进行全站下载。入口页和单篇正文分开验收；“技术上可抓取”不等于取得转载或长期保存全文的授权。

| 来源 | 入口页 | 单篇正文 | 结论与生产建议 |
|---|---|---|---|
| 亿邦动力 TEMU | 成功，含标题、日期、摘要和链接 | 失败，两次均触发 `haplat` 异常行为拦截 | 可作为元数据线索源；正文保留原文链接，暂不依赖自动全文抓取 |
| 雨果跨境 TEMU | 成功，含文章列表和时间 | 成功 | 可做正文提取和 AI 分析；保留作者、来源及原文链接 |
| AMZ123 TEMU | 成功，含文章列表和日期 | 成功 | 可做正文提取和 AI 分析；保留出处和原文链接 |
| PDD Holdings 投资者关系 | 首页成功，但主要是公司简介、股价和活动 | 新闻发布列表及单篇正文成功 | 应把入口改为 `/news-releases`；属于 PDD 集团级官方来源，并非每篇都直接涉及 TEMU |
| SHEIN Marketplace Corporate | 成功，含 Marketplace 新闻链接 | 成功 | 可作为 SHEIN 官方来源；适合低频采集正文 |
| 亿邦动力 SHEIN | 成功，含文章列表 | 同域文章详情触发反爬 | 可作为元数据线索源；正文暂不自动保存 |
| 雨果跨境 SHEIN | 成功，含文章列表 | 同域正文抽查成功 | 可做正文提取和 AI 分析；需保留作者和原文链接 |
| 亿邦动力 AliExpress | 成功，含文章列表 | 同域文章详情触发反爬 | 可作为元数据线索源；正文暂不自动保存 |
| 雨果跨境 AliExpress | 成功，含文章列表 | 同域正文抽查成功 | 可做正文提取和 AI 分析 |
| 雨果 AliExpress 官方发布 | 成功，含文章列表 | 正文技术上可抓 | 频道名不构成逐篇官方背书；必须按作者/来源字段逐篇判断官方性 |
| AMZ123 AliExpress | 成功，含文章列表和日期 | 同域正文抽查成功 | 可做正文提取和 AI 分析；保留出处和原文链接 |
| Alibaba Group News | 成功，含标题、日期和链接 | 成功 | 官方集团来源，但需要 AliExpress 关键词/实体相关性过滤 |

## 关键技术结论

- 12 个入口页全部返回了内容，没有入口页级验证码或拒绝访问。
- 亿邦动力的频道页可用，但文章详情页连续两次返回异常行为拦截页，因此不能作为稳定正文源。
- PDD 投资者关系首页不适合作为新闻入口；`https://investor.pddholdings.com/news-releases` 能稳定返回新闻列表和摘要。
- 雨果“AliExpress 官方发布”需逐篇核验作者或原始来源，不能把整个频道统一提升为官方信源。
- 抓取产物保存在已加入 `.gitignore` 的 `.firecrawl/` 目录，不进入版本库。

## 正式接入结果

2026-08-18 已将正文抽查成功的 9 个来源设为 `allowed + enabled + full_text`：雨果 TEMU、AMZ123 TEMU、PDD Holdings 新闻发布、SHEIN Marketplace Corporate、雨果 SHEIN、雨果 AliExpress、雨果 AliExpress 官方发布、AMZ123 AliExpress、Alibaba Group News。

项目内逐源探测结果均为 `success`，每个来源均创建 1 个可持久化文档，健康状态为 `healthy`。PDD 入口已调整为 `https://investor.pddholdings.com/news-releases`。亿邦动力 3 个频道继续保持 `pending_review + disabled + metadata_only`，原因是文章详情页连续两次触发反爬拦截。
