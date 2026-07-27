# 公开来源采集运维手册

本手册用于受控运行公开来源采集。系统不使用登录态，不绕过验证码、付费墙、访问限制或
平台授权要求。来源条款、robots 或技术访问边界任一不明确时，保持 `pending_review` 或
`authorization_required`，并设置 `enabled: false`。

## 安装

Python 版本要求为 3.11 或 3.12。常规安装不包含浏览器：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ingestion]"
Copy-Item .env.example .env
```

生产环境暂不开放浏览器采集。必须保持 `INGESTION_BROWSER_ENABLED=false`；飞书运行时和
采集 CLI 会在创建数据库、HTTP、浏览器或网络资源前拒绝 `true`。安装 Playwright 可选依赖
不会使该能力在生产组合中可达。

浏览器 collector 的隔离单元能力仍保留。只有 HTTP 与浏览器路径统一使用 10 MiB 响应上限、
全局并发限制和逐域名速率预算后，才能重新评审生产开放；本版本尚未统一这些预算。

## 来源登记表

版本控制内的登记表位于 `src/commerce_agent/sources/public_sources.yaml`。每条记录包含：

| 字段 | 含义 |
|---|---|
| `source_id`, `name`, `entry_url` | 稳定标识、显示名称和公开入口 |
| `platforms`, `regions`, `language_hint` | 平台、地区和可选语言提示 |
| `trust_tier` | `official` 或 `media`；媒体内容不能当作官方政策 |
| `collector`, `collector_config` | `rss`/`sitemap`/`html`/`api`/`browser` 及有界解析配置 |
| `compliance` | `allowed`、`pending_review`、`denied` 或 `authorization_required` |
| `enabled`, `interval_minutes` | 是否实际采集及单来源间隔；只有 `allowed` 可启用 |
| `terms_url`, `robots_url` | 必须来自发布方的条款和 robots 证据 |
| `reviewed_at`, `compliance_notes` | 最近人工复核日期及可审计结论 |

修改来源前，分别打开官方入口、官方 robots 和官方条款，记录状态、重定向、访问边界和
复核日期。只认可发布方的一手页面；不得把搜索摘要、第三方博客或一次成功请求视为授权。
遇到 401/403、登录、验证码、付费墙、异常跨域重定向、条款禁止或证据不足时，不要更改
User-Agent、代理、Cookie 或安全规则来绕过；应禁用来源并转为人工复核。

2026-07-20 的 eBay RSS 复核依据为官方 [Press Room](https://www.ebayinc.com/stories/press-room/)、
[robots.txt](https://www.ebayinc.com/robots.txt) 和
[Terms of Use](https://www.ebayinc.com/terms-of-use/)。Press Room 明确邀请将 RSS URL 加入
RSS reader；robots 为通用客户端设置 2 秒 crawl delay，未禁止 RSS 路径。该结论仅覆盖
RSS feed，不自动批准新闻详情页采集。

媒体发现层的逐站结论记录在
[`media-source-compliance-review-2026-07-22.md`](media-source-compliance-review-2026-07-22.md)。
GDELT 只负责发现，不能替代原出版商授权。只有代码目录中明确标记为 `allowed_public` 的
出版商才能请求原文页；`licensed_api` 必须走单独的授权连接器。其他状态不得请求原文，
元数据也不会进入 LLM。新增媒体必须先更新该审计表和目录测试，再执行一次受控冒烟。

10 个 `media-gdelt-*` 元数据发现源保持启用；原文二次抓取由
`GDELT_ORIGINAL_FETCH_ENABLED` 独立控制并默认关闭。发现请求若返回 429 或
`rate_limited`，不要增加重试次数或改用代理；本轮共享 host 熔断会停止其余同域请求。
等待出口限流解除后，可逐一执行 `probe --source media-gdelt-amazon` 等发现探测。
`probe` 明确禁止原文二次抓取；只有经批准的受控 `run --source` 才能验证开启后的原文路径。

## 手动命令与退出码

```powershell
python -m commerce_agent.ingestion_cli sources list
python -m commerce_agent.ingestion_cli sources coverage
python -m commerce_agent.ingestion_cli probe --source <disabled-allowed-source-id>
python -m commerce_agent.ingestion_cli run --source <source-id>
python -m commerce_agent.ingestion_cli run --all
python -m commerce_agent.ingestion_cli health
```

- `sources list` 只加载登记表，不初始化数据库、HTTP 客户端、飞书或 DeepSeek。
- `sources coverage` 按十个平台显示 enabled/allowed/需授权/待审核/禁止/总候选数，
  不会把候选来源误报成已经接通。
- `probe --source` 只允许 `compliance=allowed` 的来源忽略 `enabled=false` 做一次手工探测；
  不修改登记表，也不放宽任何网络安全或出版机构门控。
- `run --source` 先做轻量来源校验；未知来源返回 2，且不会回显输入值。
- 退出码 0 表示成功或合规跳过，2 表示参数/目标错误，3 表示部分失败、运行失败或清理失败。
- 输出只用于运维摘要；不得包含或粘贴秘密、Cookie、Authorization、完整查询串或绑定码。

首次上线先运行 `sources list`，再对单个已复核的 `allowed` 来源运行 `run --source`，检查
退出码和 `health` 后才考虑 `run --all`。

## 调度器

`INGESTION_SCHEDULER_ENABLED=false` 是安全默认值。设置为 `true` 前必须同时满足：

1. 单来源人工运行成功，且 health/快照符合预期；
2. 来源仍为 `allowed`，robots 和条款复核未过期；
3. 运维人员明确决定启用自动采集。

调度器与飞书机器人在同一进程运行，使用 `INGESTION_INTERVAL_MINUTES`（默认 120 分钟），
不会在启动瞬间补跑。不要由脚本或部署任务静默修改用户的 `.env`。任何 `.env` 修改只在
进程启动时读取，因此修改后必须正常停止并重启 `python -m commerce_agent`。

### 熔断与人工恢复

同一来源连续 3 次 `failed` 或 `partial` 运行后，健康状态会变为 `suspended`。后续定时调度会以
`source_circuit_open` 跳过该来源，不发起网络请求，也不会自动解除熔断。先检查最近运行记录、快照、
来源合规状态和错误码；不得通过放宽网络安全规则、登录、Cookie、代理或提高频率来消除故障。

确认来源仍为 `allowed + enabled` 且修复或证据已完成后，运维人员使用 `python -m commerce_agent.ingestion_cli run --source <source-id>`
执行一次受控手动运行。仅手动运行成功才会将连续失败计数清零并恢复 `healthy`；手动运行失败或部分成功
继续累计失败，仍保持或重新进入 `suspended`。恢复后运行 `python -m commerce_agent.ingestion_cli health` 确认状态，
再允许下一次定时调度。

## 数据库与快照

- `DATABASE_URL` 默认指向工作目录下的 `commerce_agent.db`；生产环境应使用权限受控的路径。
- `SNAPSHOT_DIR` 默认是 `./data/snapshots`，按日期、来源 ID 和 SHA-256 压缩保存原始响应。
- 所有临时媒体 `full_text` 正文和原始快照最多保留 7 天。进程启动时及独立的每小时保留任务
  都会全局清理，不依赖来源是否启用、合规、健康、仍在登记表或再次运行。
- `.db`、`data/`、日志和 `.env` 已被 Git 忽略；备份同样必须限制访问。
- 快照不得保存 Cookie、Authorization、令牌或请求查询串。不要手动编辑已入库版本。

## 健康状态

`health` 按来源显示最近尝试、最近成功、连续失败、下次计划时间和安全错误码：

- `unknown`：尚无完成的运行记录；
- `healthy`：最近运行成功，或来源因合规/禁用被安全跳过；
- `degraded`：最近运行部分成功；
- `error`：最近运行失败。

连续失败或 `compliance_review_required`、401/403、异常重定向应触发人工复核。先确认来源
状态和证据，再检查 DNS、TLS、媒体类型、解析候选和存储权限；不要通过放宽 SSRF、响应
大小、重定向、登录或速率限制来消除错误。

## 受控真实来源冒烟

默认测试不会访问网络：

```powershell
python -m pytest tests/smoke/test_public_sources.py -q
```

只有明确批准真实网络验证时才运行：

```powershell
$env:RUN_PUBLIC_SOURCE_SMOKE = "1"
python -m pytest tests/smoke/test_public_sources.py -q
Remove-Item Env:RUN_PUBLIC_SOURCE_SMOKE
```

测试仅选择静态白名单中的 `allowed + enabled + official` 来源。当前只请求一次 eBay RSS
列表，不抓取详情；客户端关闭重试和重定向，并只断言可达性、安全媒体类型和至少一个
候选。网络失败只生成复核记录，不自动改写合规状态。

## 回退

1. 设置 `INGESTION_SCHEDULER_ENABLED=false` 并重启进程；飞书机器人继续运行。
2. 将异常来源设为 `enabled: false`，并按证据改为 `pending_review` 或更严格状态。
3. 保留数据库表、运行记录和快照用于排查，不执行破坏性删除。
4. 如需回退登记表或代码，使用经过评审的 Git revert；回退后重新运行 `sources list`、
   单来源人工命令和 `health`。

## 十平台覆盖与授权数据商

十平台严格覆盖按“每个平台 2 个独立有效发布者”验收。摘要和元数据线索不计入有效来源，
同一发布者的不同语言或站点 URL 只计一次。当前覆盖和逐来源证据以
[`ten-platform-source-acceptance.md`](ten-platform-source-acceptance.md) 为准；未达 2 / 2
的平台仍可在部分覆盖模式下运行，但日报必须如实标明。

新增来源按以下顺序推进：

1. 优先接入官方 RSS、公开 API、获准邮箱或经人工审核的官方全文投稿。
2. 官方来源不足时，再采购具有全文使用权的授权媒体数据商。
3. 授权媒体适配器实现 `LicensedNewsProvider` 契约；未配置时使用禁用适配器，必须零请求、
   零副作用。
4. 数据商凭据使用 `SecretStr`，只从环境变量或密钥系统注入，不写入 YAML、SQLite、日志、
   日报或错误消息。
5. 上线前通过 provider 契约测试、受控单来源 smoke，并连续观察至少 3 轮健康检查。

遇到 401、403、429、验证码或登录墙时停止采集并转人工复核，不绕过访问控制。官方通知
邮箱保持默认关闭；需要回退时关闭 `OFFICIAL_NOTICE_EMAIL_ENABLED` 并重启，不删除审计记录。

## GDELT 受控原文抓取

10 个 `media-gdelt-*` 来源已启用为元数据新闻雷达，每个平台每 120 分钟最多执行一个包含
25 条候选的有界查询。原文抓取与发现源相互独立：

1. 保持 `GDELT_ORIGINAL_FETCH_ENABLED=false` 完成首次发现 smoke。
2. 确认 GDELT 未返回 429，并且审核目录中至少一个 `allowed_public` 发布者能通过正文质量门。
3. 将本机 `.env` 中该值改为 `true` 后重启机器人。
4. 每个 GDELT 平台源每轮最多尝试 5 篇原文；失败仍保留标题和链接。

准入条件包括公开 HTTPS、SSRF 安全检查、最多 3 次重定向、10 MiB 响应上限、HTML 媒体
类型、至少 300 个可见字符、明确的平台名称且不存在访问墙或版权限制标记。未知发布者及
`authorization_required`、`licensed_api`、`metadata_only`、`denied` 状态均不发起原文请求。

所有 GDELT 和直连媒体临时全文及原始快照在 7 天边界统一清理，不区分分析完成、待分析、
失败或来源状态；到期但仍待处理的分析任务会以 `media_body_expired` 安全终止。分析结果、
短证据、哈希、归属和原文链接继续保留。回退只需设置
`GDELT_ORIGINAL_FETCH_ENABLED=false` 并重启；10 个新闻发现源仍继续提供待核实线索。
