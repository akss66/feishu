# Whole-branch final-review fix report

修复基线：`b3f4e200554b97ab7d031ae8f9a14e4a21433e45`

整改主体提交：`3d90a7e` (`fix: close whole-branch ingestion review findings`)

执行日期：2026-07-27（Asia/Shanghai）

执行约束：全程离线；未执行真实 GDELT、媒体站点或公共机构站点请求。

## 结论

`final-review.md` 中的 2 个 Critical、6 个 Important、3 个 Minor 均已修复并由自动化测试覆盖。全量测试、Ruff、差异完整性和新增凭据形态扫描均通过。真实网络 smoke 保持显式 opt-in，因此本次没有伪造或覆盖历史线上证据。

## 逐项整改

| Finding | 修复结果 | 主要验证 |
| --- | --- | --- |
| Critical 1：catch-all 来源污染文章平台与严格覆盖率 | 平台匹配器返回文章级精确平台集合；集合贯穿采集、抽取、持久化、分析、报告和引用；新增 `SourceAuditedPlatform` 与 `DocumentVersionPlatform`，严格覆盖只统计审计通过的发布方/平台关系；兼容旧版本回退且空平台重试不会覆盖已有精确映射 | 真实生产 registry 集成测试证明 Amazon-only 文章只属于 Amazon，严格基线保持 `3/10、3/20` |
| Critical 2：七天正文保留边界不完整 | 新增全局数据库驱动的媒体正文清理，不依赖 registry 是否仍存在、来源启停/合规/健康/租约或分析状态；启动时执行并由独立小时调度执行；同步清理快照，并将未终态分析任务安全终止 | 覆盖 pending、failed、analyzed、disabled、suspended、removed、无后续抓取，以及 GDELT/直连媒体正文 |
| Important 1：403/429 不停止本轮来源/域名 | 新增 run-scoped 共享 circuit：403 打开来源 circuit，重试耗尽的 429 打开 host circuit；`run_all` 中跨来源共享；保留稳定错误码，阻断请求不再触网 | 两候选 HTML/API 与跨来源同域测试均证明首个终态响应后不再请求 |
| Important 2：生产允许五次跳转 | HTTP 默认、daemon runtime 和 CLI 均显式限制为最多三次跳转 | 第四跳端到端拒绝测试和 runtime/CLI wiring 测试 |
| Important 3：CLI 忽略 GDELT 开关/预算 | CLI 加载并显式传入 GDELT original-fetch 开关和单来源预算；与 runtime 一致使用三跳限制和 retention；`probe` 明确禁止 original fetch | 默认关闭、覆盖开启/预算、完整 wiring 与 probe 抑制测试 |
| Important 4：公共机构 `www` 主机被拒 | FTC 与 GOV.UK profile 明确枚举 apex 和 `www` 精确主机，仍拒绝未审计 sibling 子域 | 生产形态 `www.ftc.gov` collector 测试与 catalog sibling-negative 测试 |
| Important 5：gate 与最终抽取正文分裂 | gate 与 extractor 共享文章正文提取；在快照/持久化/分析入队前对最终归一化正文再次做长度与精确平台相关性校验 | 长导航/侧栏含平台名但正文短或无关的负例测试 |
| Important 6：公共机构被标成全球权威媒体 | 新增 `PUBLIC_AUTHORITY` 分类，分析 payload、报告与飞书呈现为 `监管/公共机构信息` | catalog、analyzer、report、delivery 测试覆盖三类证据边界 |
| Minor 1：runbook 矛盾 | 删除单一旧 GDELT 来源和 30 天保留陈述；明确十个 metadata discovery 来源、original-fetch 独立开关、probe 策略及全局七天清理 | 对已知矛盾词执行定向扫描，零命中 |
| Minor 2：`TTS` 子串误匹配 | 短 ASCII alias 使用字母数字边界匹配，长名称和中文 alias 保持原语义 | `watts`、`buttons`、`attributes` 等负例和独立 `TTS` 正例 |
| Minor 3：live smoke 证据缺精确状态/时间 | opt-in harness 生成 secret-free JSON，包含执行时间、精确状态码、请求数、接受候选数、抽取数、稳定 gate 结果和匹配平台；固定到 SDD evidence 目录 | 离线 schema/脱敏测试通过；本轮未开启网络开关，因此未生成虚假 live evidence |

## TDD 与验证证据

整改中先补充能复现 review finding 的测试，再完成最小生产修复并运行相关测试。代表性 RED/GREEN 包括：

- 旧文档版本在空平台重试时会被静态来源平台污染；新增回归测试先失败，随后修复为保留已有精确映射。
- CLI 开启 GDELT original fetch 时缺少完整 wiring；新增设置/构造验证后实现生产 wiring。
- 403/429 两候选与跨来源同域场景证明 circuit 阻止后续实际请求。
- 生产 registry 集成测试直接验证精确文章平台与严格覆盖基线。

最终验证：

```text
python -m ruff format --check <33 changed Python files>
33 files already formatted

python -m ruff check <33 changed Python files>
All checks passed!

git diff --check
passed

python -m pytest -ra -q
987 passed, 3 skipped (990 collected)

skipped:
- 2 Chinese-media live smoke tests: require RUN_CHINESE_MEDIA_SMOKE=1
- 1 public-source live smoke test: require RUN_PUBLIC_SOURCE_SMOKE=1

新增差异高风险凭据形态扫描
0 matches

运行手册已知矛盾词定向扫描
0 matches
```

## 审计说明

- live smoke 的结构化 evidence 只会在显式启用真实网络 smoke 后写入；本次离线整改没有把模拟结果包装成线上证据。
- 快照和数据库正文统一遵循七天上界；文章分析、短证据、内容哈希、来源归因和原始 URL 在正文脱敏后继续保留。
- 文章级平台映射是新数据的权威归因；对历史数据只在缺少精确映射时使用兼容回退，避免升级后丢失既有可查询性。
