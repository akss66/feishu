# 合规真实数据源与首份日报验收设计

日期：2026-07-22

状态：待用户书面确认

适用范围：本机运行的飞书跨境电商情报机器人

## 1. 背景与目标

机器人已经具备飞书收发消息、群绑定、AI 分析、风险档位、证据评分、日报编排和投递框架，但数据库尚无真实采集文档，因此当前不能生成有内容的真实日报。

本阶段目标是在不迁移云服务器、不使用登录态或浏览器抓取、不绕过网站访问限制的前提下，让机器人完成第一条可重复的真实链路：

1. 从明确允许自动访问的官方源和权威聚合源获取公开信息；
2. 保存、去重并交给 AI 提炼；
3. 输出包含风险等级、置信度、原文链接、判断依据和建议动作的日报；
4. 经人工确认后向当前已绑定的飞书测试群发送一次；
5. 验收成功后在本机按计划定时运行。

## 2. 核心决策

采用“合规优先的混合来源”方案：

- 官方公开订阅源：直接采集，证据等级为 `official`。
- GDELT：仅采集其公开 DOC API 返回的文章元数据，作为权威行业媒体的发现与索引层，证据等级为 `media`。
- 需要账号、授权或卖家身份的平台开放接口：保留连接器位置，但默认关闭，取得授权后再启用。
- 条款明确禁止机器人、爬虫或系统性抓取的网页：标记为 `denied`，绝不直接抓取。
- 保守、默认、激进三个风险档位只影响风险解释与建议强度，不改变来源的法律状态，也不能解除 `denied` 或 `auth_required` 限制。

这套方案优先确保机器人现在能跑出真实结果，同时为后续逐个平台接入官方 API 留出扩展路径。

## 3. 第一批来源矩阵

| 来源 | 覆盖范围 | 方式 | 信任等级 | 初始状态 | 处理规则 |
| --- | --- | --- | --- | --- | --- |
| Amazon Selling Partner API Changelog | Amazon / 全球 | 官方 RSS | official | 启用 | 只读取官方更新条目与原文链接 |
| eBay Newsroom RSS | eBay / 全球 | 官方 RSS | official | 保持启用 | 只读取官方新闻条目与原文链接 |
| GDELT DOC 2.0 API | 十个平台 / 全球 | 公开 JSON API | media | 启用 | 只保存文章标题、来源域名、发现时间、语言和原文链接，不跟进抓取文章正文 |
| Temu 消费者网页 | TEMU | 网页 | - | denied | 条款禁止 crawling/scraping/spider，不抓取 |
| SHEIN 集团网页 | SHEIN | 网页 | - | denied | 条款禁止 robot/spider/自动化访问，不抓取 |
| AliExpress 消费者网页 | 速卖通 | 网页 | - | denied | 条款禁止未经书面许可的系统性检索，不抓取 |
| Temu Partner Platform | TEMU | 授权 API | official | auth_required | 获得合作方账号及授权后接入 |
| SHEIN Open Platform | SHEIN | 授权 API | official | auth_required | 获得开放平台授权后接入 |
| TikTok Shop Partner API | TikTok Shop | 授权 API | official | auth_required | 获得合作伙伴授权后接入 |
| Coupang Open API | Coupang | 授权 API | official | auth_required | 获得卖家授权后接入 |
| Shopee / Ozon / Joybuy 官方接口或稳定订阅源 | 对应平台 | 待核实 | - | pending | 未确认许可、稳定性和接口约束前不启用 |

GDELT 查询必须至少包含一个平台名称：Amazon、TEMU、SHEIN、AliExpress、Shopee、eBay、Coupang、Ozon、Joybuy 或 TikTok Shop，并结合 policy、compliance、regulation、recall、lawsuit、tariff、seller 等风险词降低噪声。

美国 CPSC 和欧盟 Safety Gate 属于高价值监管来源，但其召回记录本身不表示某个平台受影响。当前模型要求每条材料预先绑定至少一个平台，若把监管记录静态绑定到全部平台会产生错误归因。因此它们不进入第一批上线范围，待系统支持“中性监管事件 + AI 动态平台映射”后再接入。

## 4. 处理流程

```text
受控 DNS 解析
  -> 来源合规状态检查
  -> RSS/API 获取与大小、超时、速率限制
  -> 解析、内容哈希和去重
  -> documents / document_versions
  -> AI 结构化分析
  -> 证据评分与风险档位解释
  -> 日报预览
  -> 人工确认
  -> 飞书测试群投递
```

GDELT 是发现层，不是正文抓取代理。系统把 API 返回的单条 JSON 元数据作为分析材料，并保留原始文章 URL 供人工查看；不会自动请求该 URL，也不会携带 Cookie、登录态或浏览器指纹访问媒体网站。

## 5. 日报内容与证据规则

每条日报项目至少包含：

- 涉及平台与地区；
- 风险等级；
- 置信度或证据分数；
- AI 提炼的一句话摘要；
- 判断依据；
- 建议动作；
- 原始来源名称、发布时间或发现时间及可点击原文链接；
- 明确区分“已核验”和“待核验”。

证据规则：

1. 官方来源继续按现有评分模型计算，达到 75 分可标记为已核验。
2. 只有一个媒体来源支撑的事件，无论其他字段多完整，最终分数上限为 70，只能进入“待核验”。
3. 至少两个不同来源独立支撑同一事件后，解除单媒体来源上限，再按现有证据模型评分。
4. AI 引用的原文片段必须能在保存的材料正文或元数据中定位；无法定位时不得作为证据。
5. GDELT 的文章标题和链接只能证明“该媒体发布了这条信息”，不能自动证明文章中的事实已被平台确认。
6. 当一天没有达到展示阈值的新材料时，发送明确的“今日无满足条件的新情报”健康卡片，不虚构内容。

## 6. 安全与合规边界

- 使用已设计的固定 Cloudflare DoH 解析器解决本机 Clash/Mihomo fake-IP 对公共地址校验的干扰；DoH 地址不可由来源配置任意指定。
- 只接受 HTTPS DoH、禁止重定向、忽略系统代理环境变量；解析失败时关闭失败，不回退到可能返回 fake-IP 的系统 DNS。
- 任何来源 URL 及 GDELT 返回的文章 URL 都必须拒绝 localhost、云元数据地址、私有/保留/非全局 IP 字面量和带用户信息的 URL。
- 下载继续执行响应大小、连接超时、全局并发和域名限速限制。
- 不使用浏览器采集，不登录卖家中心，不绕过验证码、反爬或访问控制。
- 不读取、打印、提交或自动改写 `.env`；测试开关只通过当前进程环境变量注入。
- 日志和错误信息不得包含飞书密钥、AI Key、绑定码或授权令牌。

## 7. 本机上线与验收顺序

### 7.1 实现后手工验收

1. 以当前进程环境启用 `INGESTION_DNS_MODE=cloudflare_doh`，先分别运行来源验收命令。
2. 只有通过合规检查、DNS 公网检查、HTTP 获取和解析验证的来源才可保持启用。
3. 手工执行一次真实采集，确认数据库 `documents` 和 `document_versions` 增长。
4. 手工执行 AI 回填分析，确认 `analysis_jobs` 完成且结构化结果可追溯到材料。
5. 生成日报预览，人工检查平台归属、风险、置信度、依据、建议和链接。
6. 使用显式确认参数向当前绑定的飞书测试群发送一次日报。

若 Amazon RSS 的实际 HTTPS 地址在验收时不可用、发生不安全跳转或格式不兼容，则安全失败并暂时禁用该来源，不通过降低安全规则强行接入。

### 7.2 验收后定时运行

在不改写 `.env` 的情况下，重启本机机器人进程并以进程级变量启用：

- `INGESTION_DNS_MODE=cloudflare_doh`
- `INGESTION_SCHEDULER_ENABLED=true`
- `INTELLIGENCE_ANALYSIS_ENABLED=true`
- `INTELLIGENCE_DAILY_REPORT_ENABLED=true`
- `INTELLIGENCE_ALERTS_ENABLED=false`
- `INTELLIGENCE_QA_ENABLED=false`

日报时区继续使用 `Asia/Shanghai`，发送小时沿用现有配置。日报或告警启用时，现有投递工作器负责处理 outbox；测试阶段不开放即时预警和自动问答。

## 8. 测试设计

实现必须先补测试，再写生产代码，至少覆盖：

1. 来源注册表：新来源 ID、平台、地区、信任等级、合规状态和启用状态正确。
2. Amazon 与 eBay RSS：固定样本的条目解析、链接、发布时间和去重。
3. GDELT API：固定 JSON 样本的 `articles` 路径、字段映射、条目上限和空结果。
4. 发现链接安全：恶意 localhost、私网、元数据地址、userinfo 和非 HTTP(S) 链接被拒绝或丢弃。
5. 证据策略：单一 media 来源最高 70；两个独立来源可恢复正常评分；official 不受该上限影响。
6. DoH：A/AAAA、公网/私网/混合答案、超时、畸形 JSON、HTTP 错误、重定向、代理忽略和资源关闭。
7. 离线端到端：RSS/API 固定样本可走完采集、去重、分析、日报预览，不依赖公网。
8. 真实冒烟：在本机显式开启后验证实际端点；网络或上游不稳定不得让测试套件随机失败。

## 9. 验收标准

满足以下条件才算本阶段完成：

- eBay、Amazon（若官方 HTTPS RSS 可用）和 GDELT 的来源健康状态有明确结果；失败来源安全关闭并给出可理解原因。
- 一次真实采集后数据库至少新增一条文档；若当天所有上游均无更新，则可用受控历史窗口重试，但不得伪造数据。
- 新文档已完成 AI 分析，且结果能追溯到来源和保存材料。
- 日报预览至少包含一条带引用的真实项目，或在确无更新时生成明确的健康无更新卡片。
- 已向当前绑定飞书测试群完成一次人工确认的投递，并在 outbox 中记录成功状态。
- 机器人仍可响应“帮助”“状态”等现有命令，且进程无密钥泄漏、无未处理异常。

## 10. 本阶段不做

- 不申请或代替用户配置平台卖家/合作伙伴 API 凭据。
- 不抓取条款禁止的消费者网页或媒体全文。
- 不接入需要登录、验证码、Cookie 或浏览器自动化的页面。
- 不把 CPSC、Safety Gate 等中性监管事件静态归因给全部平台。
- 不迁移云服务器。
- 不在本阶段开启即时预警和自动问答调度。

## 11. 权威依据

- Amazon SP-API 官方发布说明及 changelog RSS：<https://developer-docs.amazon.com/sp-api/changelog/october-2022-sp-api-release-announcement>
- eBay 官方 Newsroom RSS：<https://www.ebayinc.com/stories/news/rss/>
- Temu 使用条款：<https://www.temu.com/terms-of-use.html>
- SHEIN Group 使用条款：<https://www.sheingroup.com/terms-conditions>
- AliExpress 使用条款：<https://terms.alicdn.com/legal-agreement/terms/suit_bu1_aliexpress/suit_bu1_aliexpress202204182115_66077.html>
- Temu Partner Platform：<https://partner.temu.com/documentation>
- SHEIN Open Platform：<https://open.sheincorp.com/>
- TikTok Shop Partner API：<https://partner.tiktokshop.com/docv2/page/tts-developer-guide>
- Coupang Open API：<https://developers.coupangcorp.com/hc/en-us>
- GDELT 数据与使用说明：<https://www.gdeltproject.org/data.html>
- GDELT DOC 2.0 API：<https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/>
- GDELT 使用条款与署名要求：<https://www.gdeltproject.org/about.html>
- CPSC RSS 与 API（后续阶段）：<https://www.cpsc.gov/Newsroom/CPSC-RSS-Feed>、<https://www.cpsc.gov/Recalls/CPSC-Recalls-Application-Program-Interface-API-Information>
- EU Safety Gate 数据集（后续阶段）：<https://data.europa.eu/data/datasets/rapex-rapid-alert-system-non-food?locale=en>
