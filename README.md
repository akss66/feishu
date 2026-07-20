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
