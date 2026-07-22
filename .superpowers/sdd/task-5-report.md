# Task 5 report: full regression, controlled restart, and handoff

## Verification (2026-07-22)

- `python -m pytest -q`: exit 0. No failures. The only warnings were five existing
  third-party `pkg_resources` deprecation warnings from `lark_channel` / setuptools.
- `python -m ruff check src tests`: exit 0, `All checks passed!`.
- `git diff --check`: exit 0.
- Pre-documentation `git status --short`: clean. The only production / operations
  tracked change is the required runbook clarification below; this `task-5-report.md`
  file is itself tracked implementation evidence and is intentionally updated and
  committed with this task.

## Controlled local runtime

- No existing `python -m commerce_agent` process existed before this task started.
- The first root-interpreter launch was stopped because its portable Python ignored
  `PYTHONPATH` and imported the root checkout. This task started no unrelated process.
- The final hidden process is PID `16872`, exactly one process with command line
  `python.exe -m commerce_agent`, working directory `C:\Users\AKSSINA\Desktop\feishu`.
- A temporary local interpreter under ignored `logs/commerce-agent-worktree-runtime`
  preserves the required `PYTHONPATH` and places the verified worktree `src` first.
  Its import probe resolved `commerce_agent` to
  `C:\Users\AKSSINA\Desktop\feishu\.worktrees\source-compliance-audit\src\commerce_agent\__init__.py`.
- Explicit inherited flags: `INGESTION_DNS_MODE=cloudflare_doh`,
  `INGESTION_SCHEDULER_ENABLED=true`, `INTELLIGENCE_ANALYSIS_ENABLED=true`,
  `INTELLIGENCE_DAILY_REPORT_ENABLED=true`, `INTELLIGENCE_ALERTS_ENABLED=true`, and
  `INTELLIGENCE_QA_ENABLED=true`. No `.env` contents were read or recorded.
- Non-sensitive scheduler evidence is in root stderr log
  `logs/commerce-agent-20260722-162348.stderr.log`: APScheduler added the three
  IntelligenceScheduler jobs and logged `Scheduler started`; it then added
  `IngestionScheduler._run` and logged a second `Scheduler started`.

## Health handoff

- `ingestion_cli health`: exit 0. The five newly enabled IDs are all `healthy`, with
  zero failures: `ebay-press-room`, `coupang-seller-university`, `joybuy-news`,
  `joybuy-german-news`, and `joybuy-dutch-news`. None is immediately failed or
  suspended. No live collection or daily-report delivery was triggered.
- `intelligence_cli health`: exit 3 / `partial` because of pre-existing
  `analysis_failed=6` and `analysis_pending=58`; `analysis_retry_wait=0`, all outbox
  counters are zero, and the risk profile is `default`. This task did not create or
  send intelligence work.

## Source-compliance audit summary

The 17 reviewed sources finish as: 5 allowed and enabled, 8 pending-review and
disabled, 4 authorization-required and disabled, and 0 denied.

- Enabled after live smoke: `ebay-press-room`, `coupang-seller-university`,
  `joybuy-news`, `joybuy-german-news`, `joybuy-dutch-news`.
- Rollbacks / disabled: `coupang-rules-and-policies` (`blank_content` after 10 public
  links) and `coupang-global-news` (HTTP 200 but no usable content; no safe selector
  correction). Both are `pending_review`.
- Authorization required: `shopee-sg-seller-education`,
  `shopee-my-seller-education`, and `shopee-ph-seller-education` (publisher terms
  require prior written permission), plus `ebay-seller-updates` (eBay terms and robots
  require express permission).
- Other pending-review IDs: `amazon-seller-blog`, `amazon-seller-announcements`, and
  `amazon-seller-forums` (directly applicable Amazon terms / Agent Policy could not be
  verified); `ozon-seller-news`, `ozon-seller-media`, and `ozon-global-docs`
  (unresolved `__rr` redirect loops prevent stable terms and robots verification).
- Denied among these 17: none. Live smoke yielded five accepted successes and two
  rollback outcomes; the latter includes one explicit command failure and one
  zero-usable-content safety rollback.
- The authoritative evidence is
  `docs/operations/source-compliance-review-2026-07-22.md` and the live registry is
  `src/commerce_agent/sources/public_sources.yaml`. No unresolved item is represented
  as allowed; the eight pending IDs above remain explicitly unresolved.

## Circuit-breaker operations and self-audit

- Verified behavior: after three consecutive `failed` or `partial` runs, a source is
  `suspended`; scheduled runs skip it as `source_circuit_open`; a controlled manual
  source run must succeed to reset the failure counter and restore health.
- The prior runbook lacked all three steps, so
  `docs: document ingestion circuit recovery` (`13013ce`) adds the operational
  procedure as a standalone documentation-only commit. No code, registry, or audit
  file was modified by this task.
- Final limitation: intelligence health remains partial solely for the pre-existing
  six failed and 58 pending analyses noted above. All required scheduler and ingestion
  checks have fresh evidence; the temporary runtime is intentionally under ignored
  root `logs/` and may be replaced by a future Python installation that honors
  `PYTHONPATH`.
