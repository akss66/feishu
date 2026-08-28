# 跨境电商飞书情报智能体

一个运行在飞书中的跨境电商政策与平台情报产品。它把公开来源发现、合规分级、AI 分析、日报预览、即时预警和有据问答串成可审计的工作流，让团队在熟悉的群聊中接收经过来源约束的情报，而不是无依据的模型结论。

## 核心能力

- **飞书原生交互**：通过长连接接收群消息，支持绑定、状态检查、线程内异步回复和管理员命令。
- **公开来源采集**：登记来源、手动运行、健康检查和可选调度均有明确开关与审计记录。
- **来源分级**：将内容区分为 `full_text`、`summary` 和 `metadata_only`，只有满足条件的正文才能进入确定性分析。
- **情报生产**：支持待分析任务、历史回填、日报预览、人工确认发送和即时预警预览。
- **有据问答**：回答受已采集证据约束，未覆盖的平台或来源不会由模型补写事实。
- **失败安全**：浏览器采集、自动分析、日报与预警默认关闭，外部依赖异常时保留本地审计并可单项停用。

```text
公开来源 / 人工提交 → 合规与覆盖分级 → 本地存储 → AI 分析
                                                ├→ 飞书问答
                                                ├→ 日报预览 / 确认发送
                                                └→ 预警预览
```

当前严格全文覆盖为 **3 / 10 个平台、3 / 20 个有效来源**。项目将覆盖度作为可验证指标公开呈现，不把新闻线索或元数据等同于可分析全文。

## 本地准备

1. 确认 Python 3.11 可用。
2. 创建虚拟环境并安装依赖：

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
   ```

   如需使用公开来源采集 CLI，安装采集依赖：

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -e ".[dev,ingestion]"
   ```

3. 复制本地配置文件：

   ```powershell
   Copy-Item .env.example .env
   ```

4. 只在本地 `.env` 中填写：
   - `LARK_APP_ID`
   - `LARK_APP_SECRET`
   - 新创建的 `DEEPSEEK_API_KEY`
   - 自己生成的高强度 `BOT_BIND_CODE`

不要把 `.env`、密钥或绑定码提交到 Git 或发送到聊天中。

## 启动

```powershell
.\.venv\Scripts\python.exe -m commerce_agent
```

进程保持运行时，飞书开放平台的长连接状态应显示为已连接。

## 飞书测试

将机器人加入测试群，然后依次发送：

```text
@机器人 帮助
@机器人 绑定本群 <你在本地设置的绑定码>
@机器人 状态
@机器人 AI测试 用一句话说明跨境电商是什么
```

`AI测试` 会先回复“已收到，正在处理中”，随后在同一消息线程返回测试结果；处理失败时也会在同一线程提示失败。该命令仅验证 DeepSeek 连通性，不作为平台政策依据。

## 自动检查

```powershell
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

## 公开来源采集

管理员可以在不启动飞书、DeepSeek 或自动调度器的情况下检查和手动运行来源：

```powershell
.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli sources list
.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli run --source <source-id>
.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli run --all
.\.venv\Scripts\python.exe -m commerce_agent.ingestion_cli health
```

自动调度默认关闭。只有在人工运行成功、来源合规状态已复核并由运维人员明确决定后，
才可设置 `INGESTION_SCHEDULER_ENABLED=true`。修改 `.env` 后必须重启进程才会生效；
检查配置时不要打印或复制密钥、Cookie、完整查询串或群绑定码。

完整安装、来源复核、健康解释、浏览器禁用边界、数据位置与回退步骤见
[`docs/operations/source-ingestion-runbook.md`](docs/operations/source-ingestion-runbook.md)。

## 情报分析管理员验收

AI 分析、自动日报、即时预警和有据问答四个开关均默认关闭。首次验收先运行离线测试，再以
`--limit 1` 完成单篇 backfill/分析、日报预览和当前绑定测试群的手动确认发送：

```powershell
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli analyze --backfill --limit 1
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli analyze --pending --limit 1
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli report preview --date 2026-07-22
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli report send --date 2026-07-22 --confirm
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli alerts preview --since-hours 24
.\.venv\Scripts\python.exe -m commerce_agent.intelligence_cli health
```

`analyze --limit` 默认为 10、上限为 100；单篇正文进入模型前还有固定 50,000 字符硬上限，超限任务不会调用模型，并以安全错误码 `input_too_large` 进入现有失败/重试流程。`alerts preview --since-hours` 默认为 24、上限为 168。超过单批上限的 backfill 或待分析任务应拆分为多批，逐批运行并检查 `health` 后再继续。

当前只有登记表中 `allowed + enabled` 的来源可进入分析，未覆盖平台不能补写事实。自动 analysis、
日报和预警必须分别取得用户批准；出现异常时优先关闭对应单项开关并重启，保留 SQLite 审计记录。
日报 `--date` 是上海时区 09:00 窗口的结束日期；示例 `2026-07-22` 覆盖 7 月 21 日 09:00（含）
至 7 月 22 日 09:00（不含）。
完整三档策略、测试门、监控红线、安全跳过队列与恢复步骤见
[`docs/operations/intelligence-delivery-runbook.md`](docs/operations/intelligence-delivery-runbook.md)。

### 浏览器采集可用性

生产环境当前不开放浏览器采集。保持 `INGESTION_BROWSER_ENABLED=false`；启用时，运行时和
CLI 组合会安全拒绝。浏览器 collector 的单元能力保留供后续使用，但只有 HTTP 与浏览器抓取
统一使用 10 MiB 响应上限、全局并发限制和逐域名速率预算后，才能评审生产开放。

## 十平台数据覆盖说明

当前产品范围只包括 Amazon、TEMU、SHEIN、AliExpress、Shopee、eBay、Coupang、Ozon、
Joybuy 和 TikTok Shop，覆盖全球站点。采集内容分为三个等级：

- `full_text`：正文和使用权都通过验收，可进入 LLM 分析并生成风险、置信度、依据和建议动作。
- `summary`：只作为导读和链接展示，不据此生成确定性风险结论。
- `metadata_only`：只作为待核实线索，不进入 LLM。

当前严格覆盖是 **3 / 10 个平台、3 / 20 个有效来源**，属于可测试的部分覆盖版本，不代表
十平台已经全部达标。逐平台矩阵、候选来源状态和验收证据见
[`docs/operations/ten-platform-source-acceptance.md`](docs/operations/ten-platform-source-acceptance.md)。

已审核的官方公告可以在飞书中按以下格式人工提交：

```text
提交情报
平台：Amazon
官方账号：亚马逊全球开店
原文链接：https://mp.weixin.qq.com/...
正文：
这里粘贴已核对的官方公告正文
```

可选官方通知邮箱默认关闭，只有完成发件人白名单和 TLS 配置后才启用。后续采购授权媒体
全文时实现 `LicensedNewsProvider` 接口；未配置的数据商适配器保持禁用并返回空结果。

### GDELT 新闻雷达与原文抓取

十个平台各有一个 GDELT 新闻发现源，均以 `metadata_only` 模式启用。GDELT 负责返回标题、
媒体、发布时间和原文链接；它不会让新闻自动成为可分析全文，也不会提高严格全文覆盖数。

原文二次抓取使用独立开关，默认关闭：

```dotenv
GDELT_ORIGINAL_FETCH_ENABLED=false
GDELT_ORIGINAL_FETCH_MAX_PER_SOURCE=5
GDELT_MEDIA_BODY_RETENTION_DAYS=7
```

完成受控网络 smoke 后才可将第一个值改为 `true` 并重启。即使开关开启，也只请求发布者
目录中标记为 `allowed_public` 的精确域名；当前包括 FTC、GOV.UK 和欧盟官网。登录墙、
付费墙、验证码、非 HTML、正文不完整、平台无关或带第三方版权限制的页面都会降级为
“待核实线索”，不会进入 AI。

`probe` 只验证发现路径并明确禁止原文二次抓取；原文路径只能通过经批准的有界
`run --source` 验证。所有临时媒体正文与快照不论分析或来源状态都最多保留 7 天，启动时
及独立每小时任务会全局清理；摘要、短证据、哈希、归属和原文链接继续保留。
