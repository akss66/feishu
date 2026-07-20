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

仅当某个 `allowed` 来源确实需要公开页面 JavaScript 渲染，并经过人工批准后，才安装并
启用浏览器能力：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ingestion,browser]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

随后显式设置 `INGESTION_BROWSER_ENABLED=true`。浏览器采集仍不得使用持久上下文、Cookie、
登录、验证码或反爬绕过；安装浏览器依赖本身不会批准任何来源。

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

## 手动命令与退出码

```powershell
python -m commerce_agent.ingestion_cli sources list
python -m commerce_agent.ingestion_cli run --source <source-id>
python -m commerce_agent.ingestion_cli run --all
python -m commerce_agent.ingestion_cli health
```

- `sources list` 只加载登记表，不初始化数据库、HTTP 客户端、飞书或 DeepSeek。
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

## 数据库与快照

- `DATABASE_URL` 默认指向工作目录下的 `commerce_agent.db`；生产环境应使用权限受控的路径。
- `SNAPSHOT_DIR` 默认是 `./data/snapshots`，按日期、来源 ID 和 SHA-256 压缩保存原始响应。
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
