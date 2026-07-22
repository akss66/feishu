# 第一版六项整改验收（2026-07-22）

## 验收结论

第一版六项整改已完成代码验收和本机运行验收。机器人现由 Windows 计划任务
`CrossBorderCommerceAgent` 托管，登录后启动、单实例运行，并在异常退出时按分钟重试。

## 验收证据

- 全量自动化测试：`823 passed, 1 skipped`。
- Ruff：通过。
- Windows 计划任务：状态 `Running`，运行结果 `267009`（任务仍在运行）。
- 机器人进程：仅一个 `python.exe -m commerce_agent` 进程。
- 启动日志：`IntelligenceScheduler` 与 `IngestionScheduler` 均已启动。
- 平台覆盖命令：`python -m commerce_agent.ingestion_cli sources coverage` 正常输出全部十个平台。
- 智能健康检查：无待分析、无待发送；17 条既有分析失败仍作为历史审计记录保留，
  因此状态为 `partial`，本次验收未直接修改历史数据库状态。
- 当日日报预览：命令成功，当前没有符合窗口条件的新条目，因此 `selected=0`，未向飞书发送空日报。

## 六项整改状态

1. **本机稳定启动**：新增统一启动脚本和登录自启动任务；启用采集、分析、日报、告警与问答调度，
   DeepSeek 超时调整为 60 秒，日志写入 `data/runtime`。启动脚本不读取、复制、删除或重写 `.env`。
2. **GDELT 恢复路径**：新增不改变来源状态的一次性 `probe`；2026-07-22 真实探测到达 GDELT，
   外部服务返回 HTTP 429，系统准确记录为 `rate_limited`。该来源仍保持禁用。
3. **授权媒体原文路径**：来源目录明确允许且出处信息完整时，原文正文可以进入持久化与 AI 分析；
   未授权、未知或仅许可摘要的媒体仍不可抓取正文。当前未把任何真实媒体误设为已授权。
4. **平台覆盖透明度**：覆盖表固定列出 Amazon、TEMU、SHEIN、AliExpress、Shopee、eBay、
   Coupang、Ozon、Joybuy、TikTok Shop，并区分已启用、公开候选、需授权、待审核和禁止。
5. **飞书卡片可读性**：媒体条目显示中文“来源类型”和“分析依据”，区分公开原文、订阅摘要、
   仅标题/元数据；未知内部枚举不会直接展示给用户。
6. **AI 失败收敛**：模型结构错误最多尝试三次；长文章按固定预算保留开头和结尾，证据校验只针对
   实际交给模型的文本；启动超时提高到 60 秒。历史失败不会被静默删除。

## 当前真实覆盖边界

正式启用的官方公开来源目前覆盖 Amazon、eBay、Coupang 和 Joybuy。其余平台已经进入覆盖矩阵，
但尚未启用可稳定采集的官方来源；第一版没有用搜索结果或未经审核的媒体页面冒充正式数据源。

GDELT 仍受其外部限流影响。后续人工探测成功后才能评估启用；即使启用，未知发布者默认只作为
发现线索，不能直接把全文送入模型。

## 常用命令

```powershell
# 查看任务与运行状态
Get-ScheduledTask -TaskName CrossBorderCommerceAgent
Get-ScheduledTaskInfo -TaskName CrossBorderCommerceAgent

# 查看十个平台覆盖
python -m commerce_agent.ingestion_cli sources coverage

# 单次探测禁用但合规允许的来源（不改变 enabled 状态）
python -m commerce_agent.ingestion_cli probe --source media-gdelt-cross-border

# 查看智能模块健康状态
python -m commerce_agent.intelligence_cli health
```
