# Task 12 实施报告

## 交付范围

- 新增安全管理员 CLI：`analyze`、`report`、`alerts preview`、`health`；
- 为真实 SQLite repository 增加仅聚合计数的 `health_summary`；
- 新增真实 SQLite 离线验收链和 HTML fixture；
- 新增分阶段上线、监控、回滚与恢复手册，并在 README 提供入口。

未读取、打印或修改用户 `.env`，未修改 `.env.example`。没有真实网络、DeepSeek 或飞书调用。

提交按原子边界拆分为：

1. `d075c33 feat: add safe intelligence administration cli`：生产 CLI、共享 dedup/health 适配、测试和 fixture；
2. `docs: add intelligence operations and offline acceptance`：runbook、README 与本报告。

## TDD 证据

### CLI RED → GREEN

首轮：

```text
python -m pytest tests/unit/test_intelligence_cli.py -q
ERROR ModuleNotFoundError: No module named 'commerce_agent.intelligence_cli'
```

实现最小 CLI 后，25 个初始 CLI 测试通过。随后为未绑定 health、alerts 真实接口适配、owned app
关闭失败分别新增失败测试，观察到预期 `KeyError`、缺失 `alert_preview_summary` 和错误退出码，再逐项
实现。最终 CLI 单元测试为 27 个。

### Repository RED → GREEN

离线集成测试先通过核心 pipeline，但 health 测试因
`AttributeError: SqlAlchemyIntelligenceRepository has no attribute health_summary` 失败。加入聚合查询后
通过；方法只读取 job/outbox 状态并返回计数，不读取 payload。

### 离线 pipeline

`test_offline_pipeline_from_article_to_alert_report_qa_and_delivery` 使用临时真实 SQLite、生产
ContentExtractor/持久化/Analyzer/EvidenceScorer/RiskPolicy/Report/Alert/CorpusRetriever/QaService/
DeliveryWorker，外部边界仅替换为固定 JSON gateway 与 fake 飞书 port。它验证：

- fixture HTML 入库且一个版本只分析一次；
- media allowed 单来源证据分为 70，激进档生成橙色 early signal；
- early signal 去掉模型不可逆动作，仅保留固定可逆动作；
- 同一结果在默认/保守档不入队，激进同事件 24 小时去重；
- 日报选中真实 analysis ID；
- QA 回答包含 `[1]` 和入库来源链接；
- alert、report、QA 三类 Outbox 通过 fake 飞书发送，零真实网络。

## 真实接口差异与适配

任务草案引用了尚不存在的 `repository.alert_preview_summary()`。没有为测试复制或虚构该接口；CLI
改为调用真实 `list_unqueued_alert_candidates()`，再使用生产同一个 `RiskPolicy` 和当前群偏好进行
只读统计。`health_summary()` 是实际缺失且必要的管理员聚合能力，按 RED 测试最小加入 repository。

health 在没有有效群绑定时使用配置的默认 risk profile，不创建空群偏好，也不显示群 ID；report 和
alerts 仍需要真实当前绑定，缺失时返回目标错误退出码 2。

## 验收映射

| 验收项 | 测试 |
|---|---|
| 精确 CLI surface、严格正整数/日期、退出码 | `test_parser_exposes_only_the_documented_command_surface`、`test_invalid_arguments_exit_two_without_running_app`、`test_partial_or_failed_result_exits_three` |
| preview 不发送、send 必须 confirm | `test_report_preview_never_sends`、`test_report_send_requires_confirm_without_building_application`、`test_confirmed_report_send_delivers_once` |
| 安全输出与受控错误 | `test_cli_never_renders_content_secrets_or_urls_from_result`、`test_target_failure_is_safe_and_exits_two`、`test_runtime_failure_is_controlled` |
| health 只显示 profile、未绑定安全 | `test_health_reports_profile_without_exposing_group_id`、`test_production_health_uses_default_profile_when_no_group_is_bound` |
| 真实 SQLite 完整离线链 | `test_offline_pipeline_from_article_to_alert_report_qa_and_delivery` |
| 当前档位 alerts 只读预览 | `test_alert_cli_preview_uses_active_profile_without_queueing` |
| 三档阈值边界 | `test_alert_profile_boundaries` |
| 激进 60–74 橙色/可逆、保守固定动作 | pipeline 测试、`test_aggressive_early_signal_has_only_fixed_reversible_actions`、`test_conservative_alert_replaces_model_action_with_fixed_verification` |
| 日报 5–15/不填充/空覆盖 | `test_report_selects_at_most_15_unique_events_and_does_not_pad`、`test_empty_day_builds_health_report_with_platform_source_coverage` |
| 24h 去重、状态边界、升级/新版本 | pipeline 测试、`test_alert_preview_shares_pending_and_sent_dedup_without_writing`、`test_alert_preview_allows_upgrade_or_changed_version_without_writing`、`test_alert_preview_matches_queue_contract_for_terminal_rows` |
| QA 引用、拒答 | pipeline 测试、`test_qa_rejects_answer_with_missing_fact_citation`、`test_qa_safely_refuses_invalid_model_results` |
| 四 flag 默认关闭 | `test_intelligence_flags_are_safe_by_default` 与 `.env.example` 静态核验 |

三个 profile 复用同一个完成后的 `ScoredAnalysis`、EvidenceScorer、RiskPolicy 与来源合规过滤；Analyzer
中没有 profile 分支。

## 文档与上线边界

runbook 明确：四开关全关离线 → backfill 1/人工复核 → report preview → confirm 手动发送测试群 →
三档策略 → alerts fixture → 单独批准 QA → analysis/daily/alerts 分别批准。当前只承认登记表内
`allowed + enabled` 覆盖，未验证平台不补写。

回滚优先关闭单项 flag 并重启。SQLite 审计保留；如必须阻止已排队消息，先停进程并备份数据库，
只对人工确认的显式内部 ID 在 `BEGIN IMMEDIATE` 事务中把 pending/retry_wait 标为 skipped，记录
`changes()`，不删除数据库或历史记录。文档没有声称已有 dashboard、HA 或多节点能力。

## 验证结果

- `python -m pytest -v`：629 passed，1 个显式可选 smoke skipped；
- `python -m ruff check .`：通过；
- `python -m ruff format --check`（本任务 Python 文件）：通过；
- `python -m compileall -q src tests`：通过；
- `git diff --check`：通过；
- 六条文档 CLI 命令 parser 校验：6/6；缺 confirm guard：退出 2；
- `.env.example` 四 flag：全部为 false；`.env`/`.env.example` git 改动：0；
- 敏感扫描：仅生产配置边界、持久化字段和“不泄漏”测试假值命中，无真实凭据。

独立 code review 首轮发现 0 Critical、2 Important：alerts preview 未复用 24 小时去重、日报示例
日期落入错误窗口。两项均按 TDD 修复；新增 pending/sent、failed/skipped、风险升级、新版本内容变化和
preview 不写 Outbox 边界测试，README/runbook 统一使用 `2026-07-22` 并解释上海日报窗口。复审为
0 remaining Critical/Important，结论 Approve / ready to merge。

## 复审加固（2026-07-22）

针对后续独立复审的 2 项 Important 与 1 项 Minor，按 TDD 完成以下加固：

- RED：先加入操作上限与启动清理回归测试；首次收集因缺少 `MAX_BATCH_LIMIT`、
  `MAX_ALERT_PREVIEW_HOURS` 而 `ImportError`，证明测试先于实现失败；
- GREEN：`analyze --limit` 保留默认 10 并限制为 1–100，`alerts preview --since-hours`
  保留默认 24 并限制为 1–168；101/169 及超大整数都在构建应用前返回
  `error=invalid_arguments`/退出码 2，100/168 正常传给应用；
- 启动构建失败时按 channel → client → database 顺序逐项尝试清理。任一项或全部清理失败均不
  阻断后续清理，也不覆盖最初的启动异常；清理异常不进入 CLI 输出或日志；
- README 与 runbook 的新 Python 命令统一使用
  `.\.venv\Scripts\python.exe -m ...`，并说明上限、默认值和大批任务分批运行规则；
- worktree 与主工作树均没有现成 `.venv`，因此没有伪称完成该解释器路径的 help 实测。README
  已明确先创建 venv 并 editable install 的前置合同；现有嵌入式 Python 的 `._pth` 固定指向主工作树，
  不接受 `PYTHONPATH=src` 覆盖，故改用同一解释器显式前置 worktree `src` 后通过 `runpy` 实测
  `commerce_agent.intelligence_cli --help`，退出码 0 且显示四类命令；另有六条 parser 命令 6/6
  通过，100/168 接受、101/169 拒绝；
- 复审产生的两个 `tmp-review-*` 目录均只含离线 SQLite 测试库，核对绝对路径后已删除。

复审加固后的新鲜验证结果：

- 聚焦单元/集成：40 passed；
- `python -m pytest -v`：639 passed，1 个显式可选 smoke skipped；
- `python -m ruff check .`：通过；
- `python -m ruff format --check`（本任务 Python 文件）：通过；
- `python -m compileall -q src tests`：通过；
- `git diff --check`：通过；
- `.env`/`.env.example` git 改动：0；
- 敏感扫描只命中生产配置边界和“不泄漏”测试假值，无真实凭据。
