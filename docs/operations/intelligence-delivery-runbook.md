# 情报分析与交付运维手册

本手册只覆盖仓库当前实现的 SQLite、DeepSeek、飞书单一有效群绑定和管理员 CLI。当前没有
dashboard、高可用、多节点协调或自动灾备能力。所有自动能力必须逐项批准；不得因为离线验收通过
就推断生产来源、模型结果或飞书交付已经验证。

## 安全默认值与已知覆盖

以下四个开关默认都是 `false`，首次验收期间保持全关：

```dotenv
INTELLIGENCE_ANALYSIS_ENABLED=false
INTELLIGENCE_DAILY_REPORT_ENABLED=false
INTELLIGENCE_ALERTS_ENABLED=false
INTELLIGENCE_QA_ENABLED=false
```

不要由脚本读取、打印或改写用户现有 `.env`。人工修改开关后必须正常停止并重启
`python -m commerce_agent` 才会生效。

来源覆盖只以 `python -m commerce_agent.ingestion_cli sources list` 显示的
`allowed + enabled` 记录为准。当前登记表中只有 eBay Newsroom RSS 满足该条件；其他平台没有已
验证更新时，日报必须显示“无已验证更新”或“尚无合规启用来源”，不能补写平台事实，也不能把
一次人工访问成功解释为长期授权。

## 管理员命令与退出码

```powershell
python -m commerce_agent.intelligence_cli analyze --backfill --limit 1
python -m commerce_agent.intelligence_cli analyze --pending --limit 1
python -m commerce_agent.intelligence_cli report preview --date 2026-07-22
python -m commerce_agent.intelligence_cli report send --date 2026-07-22 --confirm
python -m commerce_agent.intelligence_cli alerts preview --since-hours 24
python -m commerce_agent.intelligence_cli health
```

退出码 0 表示成功，2 表示参数、目标或确认错误，3 表示运行失败或部分失败。命令输出只包含状态、
计数、内部 ID、安全错误码和当前 `risk_profile`；不会显示文章正文、提示词、模型原始输出、用户
问题、群 ID、绑定码、密钥、完整 URL 或查询串。`report preview` 只保存预览，不创建 Outbox；
`report send` 缺少 `--confirm` 时不会初始化发送路径、入队或发送。未绑定时 `health` 使用安全默认
档位且不显示群标识；需要目标群的 report/alerts 命令返回目标错误。

## 分阶段验收

每阶段失败都停止推进。不要合并阶段，也不要预先打开下一阶段开关。

### 0. 全关离线验证

保持四个开关全为 `false`，运行默认无网络测试：

```powershell
python -m pytest -v
python -m ruff check .
python -m compileall -q src tests
git diff --check
```

确认离线链覆盖 fixture HTML 入库、fake JSON 分析、证据评分、三档策略、日报、预警、带引用问答、
Outbox 和 fake 飞书发送。不要设置真实网络或 smoke 开关。

### 1. 单篇 backfill 与人工复核

在已有一篇 `allowed` 来源文章的测试数据库上依次运行：

```powershell
python -m commerce_agent.intelligence_cli analyze --backfill --limit 1
python -m commerce_agent.intelligence_cli analyze --pending --limit 1
```

在受控本地数据库查看该内部 analysis ID，对照原文逐项复核中文摘要、风险、证据可信度、判断依据、
建议动作和来源。不要把正文或结构化 payload 复制到共享日志。任何证据引文无法在原文定位、平台或
范围被模型自行补写、不可逆动作未经过人工复核，均为停止红线。

### 2. 日报预览

```powershell
python -m commerce_agent.intelligence_cli report preview --date 2026-07-22
```

`--date` 是日报结束日期；`2026-07-22` 对应 `Asia/Shanghai` 的 2026-07-21 09:00（含）至
2026-07-22 09:00（不含）。应使用覆盖刚才入库文章的结束日期，不能把文章发布日期直接当作
`--date`。

在本地 SQLite 查看对应 `daily_reports` 预览记录，确认动态选择 5–15 条且不为凑数填充；不足 5 条
就只显示真实条目，空窗口仍生成健康日报并明确覆盖不足。确认 `delivery_outbox` 没有因 preview
新增日报行。

### 3. 手动发送到测试群

先确认当前唯一有效绑定确实是专用测试群，再执行：

```powershell
python -m commerce_agent.intelligence_cli report send --date 2026-07-22 --confirm
```

验收同群同日报日期只有一个 Outbox 幂等键和一条飞书消息。目标不是测试群、出现多条消息、发送
内容与刚才预览不一致，立即停止。此阶段仍不批准自动日报。

### 4. 三档策略

在当前绑定测试群依次发送：

```text
策略 保守
策略 默认
策略 激进
```

核对回复阈值分别为：保守仅高风险且 `>=85`，默认中/高风险且 `>=75`，激进中/高风险且
`>=60`。每次切换后重启进程并执行 `策略`，确认偏好持久化；在未绑定群发送修改命令必须被拒绝。
第一版只验证当前有效群绑定，不得声称已验证飞书管理员身份。

### 5. 预警 fixture 与安全动作

使用离线 fixture 或经人工复核的测试数据验证：

- 保守档中风险不进入 Outbox，高风险只有 `>=85` 才进入；
- 默认档中/高风险只有 `>=75` 才进入；
- 激进档 `60–74` 只能生成橙色“早期信号·待核实”，`>=75` 的高风险才使用红色；
- 相同事件 24 小时内去重，风险升级允许重发；卡片与问答引用都指向已入库来源；
- 保守预警只显示固定“人工复核后再决定”动作；激进早期信号只显示固定的核对、负责人和可逆
  准备动作，二者都不能显示模型提出的未复核不可逆动作。

可先运行不发送的摘要：

```powershell
python -m commerce_agent.intelligence_cli alerts preview --since-hours 24
```

### 6. 仅开启有据问答

单独取得用户批准后，只设置 `INTELLIGENCE_QA_ENABLED=true` 并重启。测试同一 thread 的追问、
每个事实段落引用和资料不足固定拒答。引用缺失、跨群上下文、模型使用入库外事实或拒答异常升高时，
立即恢复为 `false` 并重启。

### 7. 三项自动能力分别批准

必须分别向用户申请并记录批准，之后才能逐项开启：

1. `INTELLIGENCE_ANALYSIS_ENABLED`：生产自动 analysis drain；
2. `INTELLIGENCE_DAILY_REPORT_ENABLED`：自动日报；
3. `INTELLIGENCE_ALERTS_ENABLED`：自动即时预警。

一次批准不能代替另外两项。真实上线在预览和手动测试群发送后暂停，直到对应批准到位。

## 监控、成功门与红线

`python -m commerce_agent.intelligence_cli health` 提供待分析、分析重试/失败、Outbox 待发送、重试/
失败和当前档位的安全摘要。当前没有内建 dashboard；发布期间应定时保存这些计数及进程受控日志中的
内部 job ID、耗时和安全错误码。另行人工记录以下指标，不把正文或用户问题写入监控：

- 分析领取/成功/失败/重试数，模型调用预算和结构校验失败数；
- 风险等级与证据分数档位分布，预警候选、去重和升级重发数；
- 日报预览/入队/发送数，Outbox pending/retry_wait/failed 数；
- 问答总数、拒答率和引用校验失败数。

继续下一阶段的成功门：本阶段聚焦测试全绿；健康摘要无增长中的 retry/failed；预期消息计数精确；
人工抽检证据、档位、动作和引用均通过。以下任一情况立即回滚：秘密或正文出现在日志、无来源事实、
激进 60–74 非橙色待核实、保守中风险入队、不可逆模型动作进入受限卡片、同事件 24 小时重复发送、
日报重复发送、引用无效、目标群不确定、失败/重试持续增长。

## 单项回滚与恢复

回滚优先只关闭发生问题的开关并重启，不要顺带改变其他能力：

1. 将受影响的 `INTELLIGENCE_*_ENABLED` 设置为 `false`；
2. 正常停止并重启 `python -m commerce_agent`，阻止新 job；
3. 运行 `health`，记录安全计数和内部 ID；
4. 保留 SQLite、分析、日报和 Outbox 审计记录，不删除数据库、不清空表、不重写历史 payload；
5. 修复后从离线测试和单篇/单消息阶段重新开始，再单独取得批准。

如果关闭开关后必须阻止已经排队的特定消息，先停止进程，人工核对确切内部 Outbox ID，再在 SQLite
事务中执行下列安全 SQL。执行前先备份实际 `DATABASE_URL` 指向的 SQLite 文件；默认路径可使用：

```powershell
Copy-Item -LiteralPath .\commerce_agent.db -Destination .\commerce_agent.pre-rollback.db
```

如果备份目标已存在，换用新的受控文件名，不要覆盖旧备份。只允许显式 ID，禁止无 `WHERE` 更新：

```sql
BEGIN IMMEDIATE;
SELECT id, message_kind, status, attempt_count
FROM delivery_outbox
WHERE id IN (101, 102);

UPDATE delivery_outbox
SET status = 'skipped',
    safe_error_code = 'operator_rollback',
    next_attempt_at = NULL,
    lease_token = NULL,
    lease_expires_at = NULL
WHERE id IN (101, 102)
  AND status IN ('pending', 'retry_wait');

SELECT changes();
COMMIT;
```

将示例 ID 替换为已复核的真实内部 ID，并保存变更数作为审计证据。`sending` 行不在该操作范围内；
先保持进程停止并调查 lease，不能强行覆盖。恢复时确认跳过行仍可查询、健康计数稳定、目标测试群正确，
然后按阶段重新预览和手动发送。绝不通过删除 `.db`、`delivery_outbox` 或历史分析来“恢复”。
