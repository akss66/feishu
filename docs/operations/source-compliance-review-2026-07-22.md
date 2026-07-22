# 官方来源合规复核（2026-07-22）

本报告复核来源登记表中 17 个 `pending_review` 官方来源。结论采用“平衡开放”口径：公开页面在无登录、Cookie、验证码、付费墙或绕过技术限制，且官方条款和目标路径的 `robots.txt` 没有明确禁止时，可进入低频单源实抓；明确禁止自动访问的来源不启用；要求事先许可或账号授权的来源标记为 `authorization_required`；关键证据冲突或无法稳定核实时继续 `pending_review`。

本次仅做合规与公开可达性探测，不使用登录态、代理、浏览器伪装或验证码绕过。`Live smoke` 在逐源生产采集器验收完成前统一记为 `pending`。

## 结论摘要

- 初步允许：7 个（eBay Press Room、3 个 Coupang、3 个 Joybuy）。
- 明确禁止：4 个（3 个 Shopee、eBay Seller Updates）。
- 需要事先授权：0 个。
- 证据或访问边界不足，继续待审：6 个（3 个 Amazon、3 个 Ozon）。

## 逐来源证据

| Source ID | Decision | Entry/access | Terms | Robots | Reason | Live smoke |
|---|---|---|---|---|---|---|
| `amazon-seller-blog` | `pending_review` | [官方博客](https://sell.amazon.com/blog/) 可匿名访问并返回文章列表。 | 登记的 [Amazon Conditions of Use](https://www.amazon.com/gp/help/customer/display.html?nodeId=508088) 在独立请求中返回 HTTP 403，无法稳定核实其对该域名和本项目处理方式的适用边界；Amazon 另有官方公告说明 2026 年起自动化系统受新的 Agent Policy 约束。 | [sell.amazon.com/robots.txt](https://sell.amazon.com/robots.txt) 返回 200，仅禁止 `/404$`、`/feedback`、`/autocomplete`、`/search`、`/rum`，未禁止 `/blog/`。 | 页面和 robots 满足公开访问条件，但直接适用的官方条款无法核实；网络或条款归属不明不能解释为许可。 | `pending` |
| `amazon-seller-announcements` | `pending_review` | [官方公告页](https://sell.amazon.com/blog/announcements) 可匿名访问并返回公告列表。 | 同上：[Amazon Conditions of Use](https://www.amazon.com/gp/help/customer/display.html?nodeId=508088) 返回 HTTP 403，无法稳定核实对 `sell.amazon.com` 的适用边界。 | [sell.amazon.com/robots.txt](https://sell.amazon.com/robots.txt) 返回 200，未禁止 `/blog/announcements`。 | 入口与 robots 可用，但直接条款证据不足，因此不把“没有读到禁止”误当作授权。 | `pending` |
| `amazon-seller-forums` | `pending_review` | [Seller Forums](https://sellercentral.amazon.com/seller-forums) 可匿名读取；[官方 FAQ](https://sellercentral.amazon.com/seller-forums/faqs) 明确公众只能只读，发帖需要有效 Seller Central 账号。 | [官方论坛指南](https://sellercentral.amazon.com/seller-forums/guidelines) 说明论坛内容属于 Amazon，使用同时受 Business Solutions Agreement、Privacy Notice 和 Amazon Conditions of Use 约束；[2026 官方更新](https://sellercentral.amazon.com/seller-forums/discussions/t/84e3f6b1-42f7-4cf3-a189-a5cc8d78d838) 又要求自动化系统识别自身并遵守 Agent Policy，但目标 Agent Policy 无法从本次公开链路稳定核实。 | [sellercentral.amazon.com/robots.txt](https://sellercentral.amazon.com/robots.txt) 返回 200，先允许 `/seller-forums`、`/forums/`，再全站 `Disallow: /`；特定 Allow 规则覆盖目标入口。 | 公开只读和 robots 均可，但绑定的 2026 Agent Policy 内容未被完整核实；在确认采集器身份声明与政策要求前继续待审。 | `pending` |
| `shopee-sg-seller-education` | `denied` | [新加坡 Seller Education Hub](https://seller.shopee.sg/edu/home) 匿名请求返回 HTTP 200。 | [Shopee Singapore Terms of Service](https://help.shopee.sg/portal/4/article/77148-Shopee-Terms-of-Service) 第 3.1 条明确禁止未经事先书面同意使用 robot、spider、自动设备或人工流程监控或复制内容（标准搜索引擎例外）。 | [seller.shopee.sg/robots.txt](https://seller.shopee.sg/robots.txt) 返回 200，仅禁止 `/account/`，未禁止 `/edu/home`。 | robots 未禁止不能覆盖条款中的明确自动采集禁令。 | `pending` |
| `shopee-my-seller-education` | `denied` | [马来西亚 Seller Education Hub](https://seller.shopee.com.my/edu/home) 匿名请求返回 HTTP 200。 | [Shopee Malaysia Terms of Service](https://help.shopee.com.my/portal/4/article/77215-Shopee-Terms-of-Service) 第 3.1 条明确禁止未经事先书面同意使用 robot、spider、自动设备或人工流程监控或复制内容。 | [seller.shopee.com.my/robots.txt](https://seller.shopee.com.my/robots.txt) 返回 200，仅禁止 `/account/`，未禁止 `/edu/home`。 | 条款明确禁止本项目所需的自动监控/复制，保持禁用。 | `pending` |
| `shopee-ph-seller-education` | `denied` | [菲律宾 Seller Education Hub](https://seller.shopee.ph/edu/home) 匿名请求返回 HTTP 200。 | [Shopee Philippines Terms of Service](https://help.shopee.ph/portal/4/article/77272-Shopee-Terms-of-Service) 第 3.1 条明确禁止未经事先书面同意使用 robot、spider、自动设备或人工流程监控或复制内容。 | [seller.shopee.ph/robots.txt](https://seller.shopee.ph/robots.txt) 返回 200，仅禁止 `/account/`，未禁止 `/edu/home`。 | 条款明确禁止本项目所需的自动监控/复制，保持禁用。 | `pending` |
| `ebay-press-room` | `allowed` | [eBay Inc. Press Room](https://www.ebayinc.com/stories/press-room/) 可匿名访问并返回近期公告；页面还明确提供 [官方 RSS](https://www.ebayinc.com/stories/news/rss/) 供 RSS 阅读器订阅。 | [ebayinc.com Terms of Use](https://www.ebayinc.com/terms-of-use/) 适用于该公司站点，未出现 robot、scraper、自动访问或需事先许可的禁止性条款。 | [ebayinc.com/robots.txt](https://www.ebayinc.com/robots.txt) 本次返回 HTTP 403 错误页，无法读取；按设计，robots 不可得不单独视为授权，也不在条款无禁止且页面公开时自动否决。 | 公司新闻页公开、条款无明确自动访问禁令，且发布方提供 RSS 替代路径；允许低频采集，真实采集失败则立即回滚。 | `pending` |
| `ebay-seller-updates` | `denied` | [eBay Seller News](https://www.ebay.com/sellercenter/news) 可匿名访问并返回更新列表。 | [eBay User Agreement](https://www.ebay.com/help/policies/member-behaviour-policies/user-agreement?id=4259) 第 3 节明确禁止未经 eBay 事先明确许可使用 robot、spider、scraper、数据挖掘/提取工具或其他自动方式访问服务。 | [ebay.com/robots.txt](https://www.ebay.com/robots.txt) 文件头也明确说明未经 eBay 明示许可使用机器人或自动方式访问站点被严格禁止。 | 条款和 robots 都直接禁止未获许可的自动访问。 | `pending` |
| `coupang-rules-and-policies` | `allowed` | [Coupang Rules & Policies](https://globalsellers.coupang.com/en/rules-and-policies/) 可匿名访问并返回政策文章列表；未跳转到登录页。 | 同一官方 [Rules & Policies](https://globalsellers.coupang.com/en/rules-and-policies/) 页面及页脚未发现针对该公开内容的自动访问禁令或事先许可要求；未发现独立站点使用条款入口。 | [globalsellers.coupang.com/robots.txt](https://globalsellers.coupang.com/robots.txt) 返回 200，`User-agent: *` 的 `Disallow` 为空。 | 官方公开页面、无登录边界、robots 全路径允许，未发现明确禁止；可低频试抓。 | `pending` |
| `coupang-seller-university` | `allowed` | [Coupang Seller University](https://globalsellers.coupang.com/en/seller-university/category/before-you-start/) 可匿名访问并返回课程文章列表。 | 同域官方公开页面和页脚未发现自动访问禁令或事先许可要求；独立站点条款入口未发现。 | [globalsellers.coupang.com/robots.txt](https://globalsellers.coupang.com/robots.txt) 返回 200，`Disallow` 为空。 | 页面公开、无需账户、robots 未禁止，符合平衡开放条件；仅采集标题、时间、正文和原链。 | `pending` |
| `coupang-global-news` | `allowed` | [Coupang Step-by-step Guide](https://globalsellers.coupang.com/en/step-by-step-guide/) 可匿名访问并返回卖家指南内容；注册/登录链接是可选外链，不是读取页面的前置条件。 | 同域官方公开页面和页脚未发现自动访问禁令或事先许可要求；独立站点条款入口未发现。 | [globalsellers.coupang.com/robots.txt](https://globalsellers.coupang.com/robots.txt) 返回 200，`Disallow` 为空。 | 目标内容在未授权状态可读，且没有明确禁止自动访问；可进入单源实抓验收。 | `pending` |
| `ozon-seller-news` | `pending_review` | [Ozon Seller News](https://seller.ozon.ru/media/news/fbo-i-fbs-gde-smotret-trebovaniya-k-upakovke/) 对匿名请求持续返回 307，并把同一路径追加 `?__rr=1`；跟随后继续递增 `__rr`，形成重定向循环。 | [Ozon Logistics Contract](https://docs.ozon.ru/legal/en/partners/logistics/contract/) 同样进入 `__rr` 重定向循环，无法核实相关自动访问条款。 | [seller.ozon.ru/robots.txt](https://seller.ozon.ru/robots.txt) 返回 307 并重复追加 `__rr`，无法取得有效 robots 内容。 | 入口、条款和 robots 都无法在不绕过限制的前提下稳定核实；网络失败不能解释为许可或禁止。 | `pending` |
| `ozon-seller-media` | `pending_review` | [Ozon Seller Media](https://seller.ozon.ru/media/news/ocenivajte-effektivnost-vashih-promokodov/) 对匿名请求进入同域 `__rr` 307 重定向循环。 | [Ozon Logistics Contract](https://docs.ozon.ru/legal/en/partners/logistics/contract/) 进入相同重定向循环，条款未核实。 | [seller.ozon.ru/robots.txt](https://seller.ozon.ru/robots.txt) 无法在重定向循环外取得。 | 无法确认公开内容、条款和目标路径 robots；不得通过代理、Cookie 或伪装绕过。 | `pending` |
| `ozon-global-docs` | `pending_review` | [Ozon Documents Topic](https://seller.ozon.ru/media/tags/dokumenty/) 对匿名请求进入同域 `__rr` 307 重定向循环。 | [Ozon Logistics Contract](https://docs.ozon.ru/legal/en/partners/logistics/contract/) 进入相同重定向循环，条款未核实。 | [seller.ozon.ru/robots.txt](https://seller.ozon.ru/robots.txt) 无法在重定向循环外取得。 | 关键证据无法稳定核实，继续禁用，等待发布方提供可公开访问的稳定入口。 | `pending` |
| `joybuy-news` | `allowed` | [Joybuy News](https://about.joybuy.com/news/) 可匿名访问并返回 2026 年新闻列表。 | [Joybuy 官方站](https://about.joybuy.com/) 页头、页脚和公开导航未提供独立站点条款，也未发现自动访问、抓取或复制的明确禁止。 | [about.joybuy.com/robots.txt](https://about.joybuy.com/robots.txt) 返回 200，仅禁止 `/wp-admin/`，明确允许 `/wp-admin/admin-ajax.php`；未禁止 `/news/`。 | 页面公开、无需登录、robots 未禁止且未发现明确条款禁令；可低频试抓。 | `pending` |
| `joybuy-german-news` | `allowed` | [Joybuy German News](https://about.joybuy.com/de/news/) 可匿名访问并返回德语新闻列表。 | 同一发布方 [Joybuy 官方站](https://about.joybuy.com/) 未发现自动访问或需事先许可的明确禁止。 | [about.joybuy.com/robots.txt](https://about.joybuy.com/robots.txt) 未禁止 `/de/news/`。 | 与英文站同一发布域，但本条仍独立验证了德语入口；公开且无明确禁止，可低频试抓。 | `pending` |
| `joybuy-dutch-news` | `allowed` | [Joybuy Dutch News](https://about.joybuy.com/nl/news/) 可匿名访问并返回荷兰语新闻列表。 | 同一发布方 [Joybuy 官方站](https://about.joybuy.com/) 未发现自动访问或需事先许可的明确禁止。 | [about.joybuy.com/robots.txt](https://about.joybuy.com/robots.txt) 未禁止 `/nl/news/`。 | 独立验证荷兰语入口可公开访问；无登录边界、robots 未禁止且无明确条款禁令，可低频试抓。 | `pending` |

## 未变更的范围外来源

以下 19 个登记来源不在本轮审核范围，未对其既有 `allowed`、`denied` 或 `authorization_required` 结论作任何变更：

`amazon-sp-api-changelog-rss`、`temu-seller-center`、`temu-about`、`temu-support-center`、`shein-group-newsroom`、`shein-group-press-releases`、`shein-group-company-updates`、`aliexpress-marketplace`、`aliexpress-seller-portal`、`aliexpress-terms-center`、`ebay-newsroom-rss`、`tiktok-shop-academy`、`tiktok-shop-policy-pulse`、`tiktok-shop-sg-seller-terms`、`media-gdelt-cross-border`、`media-marketplace-pulse`、`media-ecommercebytes-feed`、`media-digital-commerce-360-feed`、`media-reuters-retail`。

## 后续动作

1. 只把 7 个 `allowed` 候选写入登记表并保持单次候选数不超过 20、间隔 120 分钟。
2. 逐个执行真实生产采集器测试；任何 401、403、验证码、登录跳转、异常跨域、结构不匹配或零有效内容都在同一批次回滚到 `pending_review + disabled`。
3. Amazon 等待能够直接核实的 Conditions of Use / Agent Policy 与采集器身份声明要求；Ozon 等待无需绕过的稳定公开入口。
4. Shopee 与 eBay Seller News 仅在取得发布方明确书面许可或改用官方授权 API 后再复核。
