# TEMU、SHEIN、AliExpress 重点来源接入记录

## 决策

- 日报在同等风险等级下按 TEMU、SHEIN、AliExpress、其他平台排序。
- 用户提供的 23 个精确 URL 全部进入来源登记表，不能用网站首页或泛平台名称代替。
- 每条可行动情报必须保留来源名称、原文 URL、内容级别和判断依据。
- 登录页、明确禁止自动抓取的页面和需媒体授权的页面只登记，不交给 Firecrawl 绕过限制。
- 美国 CBP 与欧盟委员会公开监管页面作为三个重点平台的共享官方正文来源启用。
- GDELT 的三个重点查询在真实 smoke 失败后保持禁用，不将失败来源包装成有效覆盖。

## 已启用并通过真实抓取

| Source ID | 平台 | 内容级别 | 2026-08-18 smoke |
|---|---|---|---|
| `us-cbp-ecommerce-policy` | TEMU、SHEIN、AliExpress | `full_text` | success，created=1 |
| `eu-online-platforms-policy` | TEMU、SHEIN、AliExpress | `full_text` | success，created=1 |

两次成功均由 Firecrawl 在本机原生连接被 DNS 安全策略拒绝后提供公开正文；没有登录、交互或访问控制绕过。

## 已登记但未启用

### TEMU

- `temu-seller-center`：TEMU 卖家登录页，`denied`。
- `temu-press-corner`：TEMU 官方新闻页，`denied`。
- `media-ebrun-temu`：亿邦动力 TEMU 频道，`pending_review`。
- `media-cifnews-temu`：雨果跨境 TEMU 频道，`pending_review`。
- `media-amz123-temu`：AMZ123 TEMU 资讯，`pending_review`。
- `media-marketplace-pulse`：Marketplace Pulse，`denied`。
- `media-reuters-home`：Reuters，`authorization_required`。
- `pdd-holdings-investor-relations`：PDD Holdings IR，`pending_review`。

### SHEIN

- `shein-group-newsroom`：SHEIN Group Newsroom，`denied`。
- `shein-marketplace-corporate`：SHEIN Marketplace Corporate，`pending_review`。
- `media-reuters-shein`：Reuters SHEIN 专题，`authorization_required`。
- `media-ebrun-shein`：亿邦动力 SHEIN 频道，`pending_review`。
- `media-cifnews-shein`：雨果跨境 SHEIN 频道，`pending_review`。

### AliExpress

- `aliexpress-seller-portal`：速卖通卖家登录页，`authorization_required`。
- `aliexpress-new-seller-landing`：官方新卖家页面，`authorization_required`。
- `media-ebrun-aliexpress`：亿邦动力 AliExpress 频道，`pending_review`。
- `media-cifnews-aliexpress`：雨果跨境 AliExpress 频道，`pending_review`。
- `media-cifnews-aliexpress-platform-news`：雨果平台发布频道，`pending_review`。
- `media-amz123-aliexpress`：AMZ123 AliExpress 资讯，`pending_review`。
- `media-reuters-aliexpress`：Reuters AliExpress 专题，`authorization_required`。
- `alibaba-group-news`：Alibaba Group News，`pending_review`。

## GDELT smoke

| Source ID | 结果 | 安全错误码 |
|---|---|---|
| `media-gdelt-temu` | failed | `firecrawl_transport_error` |
| `media-gdelt-shein` | failed | `extraction_error` |
| `media-gdelt-aliexpress` | failed | `extraction_error` |

三个来源保留 `allowed + disabled + metadata_only`。只有修复传输/响应契约并重新通过 controlled smoke 后才能启用。

## 当前覆盖变化

- 来源登记表：66 → 83 个。
- 真正启用来源：14 → 16 个。
- 有启用来源的平台：5/10 → 7/10。
- TEMU 启用来源：9 个（7 个加拿大政府摘要 + 2 个共享监管正文）。
- SHEIN 启用来源：2 个共享监管正文。
- AliExpress 启用来源：2 个共享监管正文。
- Shopee、Ozon、TikTok Shop 仍无启用来源。

## 后续授权动作

1. 优先向亿邦动力、雨果跨境、AMZ123 申请 RSS、API 或书面自动抓取/摘要授权。
2. Reuters 与 Marketplace Pulse 优先采用商业授权数据源，不直接抓正文。
3. PDD Holdings、SHEIN Corp、Alibaba Group 完成 terms、robots、选择器与 90 天相关性验收。
4. 卖家后台通过官方 API、通知邮箱或人工提交接入，不把 Cookie、密码或登录态交给 Firecrawl。
