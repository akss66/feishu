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

完整安装、来源复核、健康解释、浏览器 opt-in、数据位置与回退步骤见
[`docs/operations/source-ingestion-runbook.md`](docs/operations/source-ingestion-runbook.md)。
