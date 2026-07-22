# 跨境电商飞书情报智能体

当前里程碑提供飞书长连接、单群绑定、状态命令和 DeepSeek 连通性测试。

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
python -m commerce_agent.intelligence_cli analyze --backfill --limit 1
python -m commerce_agent.intelligence_cli analyze --pending --limit 1
python -m commerce_agent.intelligence_cli report preview --date 2026-07-22
python -m commerce_agent.intelligence_cli report send --date 2026-07-22 --confirm
python -m commerce_agent.intelligence_cli alerts preview --since-hours 24
python -m commerce_agent.intelligence_cli health
```

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
