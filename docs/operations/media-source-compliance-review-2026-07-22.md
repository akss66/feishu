# 权威媒体来源合规复核（2026-07-22）

## 结论

GDELT 仅作为新闻发现层。系统每次最多请求一次 DOC 2.0 ArtList JSON、最多接收 50 条，
再按代码内出版商目录核验原文域名。未知出版商和 `denied` 出版商直接丢弃。

原文访问按以下状态强制执行：

- `allowed_public`：可请求公开原文页，并保存最多 30 天的原始快照；
- `licensed_api`：只能通过另行批准、已配置凭据的官方授权 API；
- `authorization_required`、`metadata_only`：只保存 GDELT 元数据，不访问原文页；
- `denied`：发现结果也不进入流水线。

本次复核没有任何出版商达到 `allowed_public`。因此当前版本可以安全发现这些媒体的文章
线索，但不会把索引标题或摘要冒充原文交给 LLM。要启用某家媒体的正文分析，必须先取得
书面许可或商业 API 合同，再把它改为 `licensed_api` 并实现独立连接器。

## 逐站审计表

所有 robots 地址均为发布方根域名的一手地址。robots 只说明机器访问偏好，不等同于内容
授权；条款不清楚、仅限个人用途、禁止自动访问或存在付费墙时，统一采用更严格状态。

| 出版商 | 类别 | 入口 | 条款证据 | robots | 决策 | 原因与允许范围 |
|---|---|---|---|---|---|---|
| Reuters | 全球权威 | <https://www.reuters.com/> | <https://www.reuters.com/info-pages/terms-of-use/> | <https://www.reuters.com/robots.txt> | `authorization_required` | 条款要求自动采集事先许可；仅保留发现元数据。 |
| Associated Press | 全球权威 | <https://apnews.com/> | <https://www.ap.org/terms-and-conditions/>；<https://www.ap.org/content/> | <https://apnews.com/robots.txt> | `authorization_required` | AP 明确提供内容授权，公开站点条款不批准本项目的自动存储；仅元数据。 |
| Bloomberg | 全球权威 | <https://www.bloomberg.com/> | <https://www.bloomberg.com/tos/> | <https://www.bloomberg.com/robots.txt> | `authorization_required` | 条款明确限制 scraper、robot、存储和商业再利用；仅元数据。 |
| Financial Times | 全球权威 | <https://www.ft.com/> | <https://help.ft.com/legal-privacy/terms-conditions/> | <https://www.ft.com/robots.txt> | `authorization_required` | 订阅及版权许可边界不支持未经授权的正文自动存储；仅元数据。 |
| CNBC | 全球权威 | <https://www.cnbc.com/> | <https://www.cnbc.com/nbc-universal-terms-of-service/> | <https://www.cnbc.com/robots.txt> | `authorization_required` | NBCUniversal 条款未授予本项目自动采集和二次处理权；仅元数据。 |
| BBC | 全球权威 | <https://www.bbc.com/> | <https://www.bbc.com/usingthebbc/terms/>；<https://support.bbc.co.uk/platform/feeds/NewsFeeds.htm> | <https://www.bbc.com/robots.txt> | `authorization_required` | RSS/内容使用存在用途限制，企业智能体不按个人用途推定；仅元数据。 |
| Retail Dive | 行业媒体 | <https://www.retaildive.com/> | <https://www.industrydive.com/terms-of-use>；<https://www.industrydive.com/diveaccess> | <https://www.retaildive.com/robots.txt> | `authorization_required` | Industry Dive 条款限制 page scrape/robot，并提供商业 API；仅元数据，授权后走 API。 |
| Digital Commerce 360 | 行业媒体 | <https://www.digitalcommerce360.com/> | <https://www.digitalcommerce360.com/terms-of-use/> | <https://www.digitalcommerce360.com/robots.txt> | `authorization_required` | 公开条款没有批准本项目存储正文并交给 LLM；仅元数据。 |
| EcommerceBytes | 行业媒体 | <https://www.ecommercebytes.com/> | <https://www.ecommercebytes.com/privacy-policy-terms/> | <https://www.ecommercebytes.com/robots.txt> | `authorization_required` | 转载和商业使用授权不足；仅元数据。 |
| Modern Retail | 行业媒体 | <https://www.modernretail.co/> | <https://www.modernretail.co/terms-conditions/> | <https://www.modernretail.co/robots.txt> | `authorization_required` | 条款确认版权保护但没有授予自动存储及 LLM 处理权；仅元数据。 |
| Marketplace Pulse | 行业媒体 | <https://www.marketplacepulse.com/> | <https://www.marketplacepulse.com/terms-of-use> | <https://www.marketplacepulse.com/robots.txt> | `denied` | 条款明确要求使用者不是 bot，并禁止 robot/spider/scraper；完全丢弃。 |
| 雨果跨境 | 中文行业 | <https://www.cifnews.com/> | 未定位到足以批准自动正文采集的独立公开条款 | <https://www.cifnews.com/robots.txt> | `metadata_only` | 证据不足，保守保留发现元数据。 |
| 亿邦动力 | 中文行业 | <https://www.ebrun.com/> | 未定位到足以批准自动正文采集的独立公开条款 | <https://www.ebrun.com/robots.txt> | `metadata_only` | 证据不足，保守保留发现元数据。 |
| 白鲸出海 | 中文行业 | <https://www.baijing.cn/> | 未定位到足以批准自动正文采集的独立公开条款 | <https://www.baijing.cn/robots.txt> | `metadata_only` | 证据不足，保守保留发现元数据。 |
| 36氪 | 中文行业 | <https://36kr.com/> | <https://36kr.com/userAgreement> | <https://36kr.com/robots.txt> | `metadata_only` | 未取得企业自动采集、存储和 LLM 处理的明确许可；仅元数据。 |

## 运行约束与复核触发器

- 不使用 Cookie、登录态、代理绕过、验证码处理、付费墙规避或伪装 User-Agent。
- 文章跳转仍须通过 HTTPS、域名白名单、DNS/IP、重定向、大小、超时和限速检查。
- 飞书只发送中文分析、短引文和原文链接，绝不发送整篇文章。
- 单一媒体出版商的证据置信度上限保持 70；媒体内容不作为平台官方政策依据。
- 条款、robots、授权合同或 API 产品发生变化时立即重新复核；至少每 90 天复核一次。
- 商业授权材料不得提交到 Git；只在本地秘密配置中保存凭据，并为连接器另做权限审查。

## 真实网络冒烟记录

2026-07-22 在 `INGESTION_DNS_MODE=cloudflare_doh` 下执行：

```powershell
python -m commerce_agent.ingestion_cli probe --source media-gdelt-cross-border
```

安全客户端发出 1 次逻辑请求，旧版本结果为 `retry_exhausted`、0 条发现、0 字节终态响应；
第一版整改后同类 HTTP 429 会明确记录为 `rate_limited`。只读诊断
显示系统 DNS 返回异常地址，而 Cloudflare DoH 返回 `104.197.47.124`；固定连接该正确地址后，
GDELT 服务器明确返回 HTTP 429。结论是当前出口受到 GDELT 限流，并非解析器或目录门控错误。

因此 `media-gdelt-cross-border` 保持 `enabled: false`。不得通过代理、伪装客户端、增加频率或
放宽 DNS/SSRF 规则绕过。后续在限流解除后重新运行同一命令；只有摘要为 `success`，且请求数
为 1、条目不超过 50、没有非 `allowed_public` 原文请求时，才可改为 `enabled: true`。
