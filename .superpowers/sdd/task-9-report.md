# Task 9 合规本地语料检索交付报告

## 结果

- 新增不可变 `CorpusQuery`、`CorpusCandidate`、`EvidenceDocument`，以及只依赖本地 repository 端口的 `CorpusRetriever`。
- repository 候选查询只读取 `document_analyses -> document_versions -> documents -> sources -> source_platforms`，硬性要求文档当前版本、当前来源 `compliance=allowed`、存在成功分析，并在 SQL 中先应用时间、平台、地区和风险过滤，最多返回 100 个候选。
- 时间窗口默认最近 30 天，以 `published_at` 优先、缺失时回退 `fetched_at`；上界为查询的 `now`。最终结果严格限制为最多 8 条。
- `enabled` 没有被错误地用作检索门槛：已入库且当前仍 `allowed` 的 disabled 来源仍可返回；来源后来改为 `denied` 时会在下一次查询立即排除。
- 本地排序包含英文大小写归一化、中文 2–4 字符片段、标题/摘要/证据引文权重、抓取时间衰减、风险权重及证据可信度；最终使用分析 ID 和版本 ID 打破完全相同的排序键，保证 top-k 确定性。
- 每条证据返回 `document_version_id` 与 `analysis_id` 作为稳定文档编号，并只从结构化分析的字符串引文中提取证据；畸形引文不会进入结果。
- 查询字符最多处理 2,000 个、检索词最多 40 个，限制恶意超长输入的本地计算成本。

## TDD 证据

- RED：先创建 unit/integration 测试，聚焦命令按预期在收集阶段因 `commerce_agent.intelligence.retrieval` 不存在而失败。
- GREEN：实现最小检索模块和 repository 只读查询后，unit 与真实 SQLite integration 共 `8 passed`。
- 测试覆盖中文片段与 40 词上限、标题权重、时间衰减、风险/可信度/确定性排序、默认 30 天与 8 条上限、零条 limit，以及真实 SQLite 的 current/superseded、未分析、后来 denied、allowed 但 disabled、平台、地区、风险和时间条件。
- brief 指定的 unit/integration 测试文件同名，而原测试目录不是 package；普通 pytest 会报 import file mismatch。经父任务确认，新增空 `tests/unit/__init__.py` 与 `tests/integration/__init__.py`，不改测试文件名，并通过全量回归确认未改变 fixture/import 行为。

## 安全与合规复核

- 检索路径不调用网络、DeepSeek/OpenAI、embedding 或向量数据库，不新增依赖。
- repository 使用 SQLAlchemy 表达式和参数绑定，不拼接用户输入；查询会话只执行 `SELECT`。
- 不持久化或记录全文 query、正文、聊天内容或秘密；检索模块没有 logger/print。
- URL、标题、摘要和证据引文始终作为不可信数据返回，不解析 URL、不抓取、不执行，也不将内容解释为指令。
- 未读取、创建或修改 `.env`。

## 最终验证

- 聚焦测试：`python -m pytest tests/unit/test_intelligence_retrieval.py tests/integration/test_intelligence_retrieval.py -v` -> `8 passed`。
- 全量测试：`python -m pytest -v` -> `530 passed, 1 skipped`；5 条 warning 均来自既有 `lark_channel/pkg_resources` 弃用提示。
- Ruff：`python -m ruff check .` -> `All checks passed!`。
- Format：新增检索模块、两个测试文件及 package marker 的 `ruff format --check` -> `5 files already formatted`。现有大型 repository 文件保持原有格式，仅新增段落按 Ruff 风格编写，避免整文件格式化造成无关 diff。
- Compile：`python -m compileall -q src tests` -> exit 0。
- Diff：`git diff --check` -> exit 0（仅 Git 的 LF/CRLF 工作区提示）。

## 控制方复审修复

- Important 1：原实现把时间衰减和风险权重直接加入 `lexical_score`，导致标题、摘要和证据引文均无命中的候选仍有正分。现在词法分数只计算真实命中；零命中在排序前剔除。风险、可信度、时间衰减及稳定 ID 只用于已有正命中候选的确定性排序。
- Important 1 TDD：新增英文 `shipping deadline` 和中文 `物流截止日期` 零重叠回归；修复前两例均错误返回 `score=6.0` 的高风险新候选，修复后均返回空结果。
- Important 2：原候选 SQL 只依赖 `DocumentAnalysis` 行，未证明关联分析任务达到成功终态。现在显式连接 `AnalysisJob` 并要求现有系统使用的成功状态 `status='completed'`。
- Important 2 TDD：真实 SQLite seed helper 先把正常任务置为 `completed` 再手插分析行；另构造 `completed/pending/failed` 三种任务并均手插 `DocumentAnalysis`。修复前三者全部返回，修复后仅 `completed` 返回。
- 原有 current version、当前 `compliance=allowed`、allowed 但 disabled、后来 denied、未分析、平台、地区、风险和时间过滤回归保持通过；查询仍不记录或持久化全文，也不访问网络、模型、embedding 或向量库。
- 复审后聚焦测试：`11 passed`。
- 复审后全量测试：`533 passed, 1 skipped`；5 条 warning 仍全部来自既有 `lark_channel/pkg_resources` 弃用提示。
- 复审后质量门：`ruff check .`、新增/修改独立文件 `ruff format --check`、`compileall`、`git diff --check` 均通过。

## 预截断漏检复审修复

- RED：先新增真实 SQLite 回归，构造 101 条 30 天内、满足 current version、`compliance=allowed`、`AnalysisJob.status=completed` 等全部硬过滤但与查询无关的近期文档，以及 1 条 29 天内较旧且唯一命中 `needle` 的文档。修复前聚焦用例稳定失败，实际结果为 `[]`，证明 `fetched_at DESC LIMIT 100` 会在词法匹配前截掉唯一证据。
- GREEN：repository 新增 `(fetched_at, analysis_id)` 排序游标，retriever 以每批最多 100 条扫描全部硬过滤候选；每批沿用 Python 精确词法评分，并把内存榜单持续裁剪为最多 8 条。回归用例修复后通过，较旧唯一命中文档被返回。
- 权衡：未在 SQLite 使用 `LIKE` 粗过滤。SQLite 内建 `lower()` 与 Python `casefold()` 的 Unicode 语义不完全一致，直接粗过滤可能对中文或特殊大小写制造新假阴性；游标分页保持精确词法语义，同时避免一次性无界加载。代价是无命中查询可能读取多页，但每个 SQL 查询最多 100 行、内存候选始终最多 8 条，且游标条件与稳定排序一致。
- 安全与边界：分页查询仍只读、使用 SQLAlchemy 参数绑定，不记录或持久化 query，不访问日志、网络、模型或向量库；current version、当前 allowed、completed job、allowed 但 disabled、时间、平台、地区及风险硬过滤均保持在每页 SQL 中，最终结果仍严格最多 8 条。
- 聚焦测试：`python -m pytest tests/unit/test_intelligence_retrieval.py tests/integration/test_intelligence_retrieval.py -v` -> `12 passed`。
- 全量测试：`python -m pytest -v` -> `534 passed, 1 skipped, 5 warnings`；warning 均来自既有 `lark_channel/pkg_resources` 弃用提示。
- 质量门：`python -m ruff check .` 与 `python -m compileall -q src tests` 均通过；最终 `git diff --check` 与 staged secrets 检查在提交前执行。
