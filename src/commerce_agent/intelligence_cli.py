"""Safe administrator CLI for intelligence analysis and delivery."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Literal, NoReturn, Protocol, TextIO

from lark_channel import FeishuChannel, LogLevel, SecurityConfig
from openai import AsyncOpenAI

from commerce_agent.config import Settings
from commerce_agent.integrations.deepseek import DeepSeekGateway
from commerce_agent.intelligence.analyzer import InvalidModelOutput
from commerce_agent.intelligence.reports import ReportAlreadySent, ReportWindowOpen
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.group_bindings import SqlAlchemyGroupBindingStore
from commerce_agent.runtime import IntelligenceRuntime, _build_intelligence

_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*\Z")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
MAX_BATCH_LIMIT = 100
MAX_ALERT_PREVIEW_HOURS = 168
_SAFE_RESULT_KEYS = frozenset(
    {
        "status",
        "risk_profile",
        "claimed",
        "succeeded",
        "failed",
        "created",
        "selected",
        "sent",
        "skipped",
        "pending",
        "retry_wait",
        "analysis_pending",
        "analysis_retry_wait",
        "analysis_failed",
        "outbox_pending",
        "outbox_retry_wait",
        "outbox_failed",
        "outbox_sent",
        "reports_previewed",
        "alerts_eligible",
        "high",
        "medium",
    }
)
_SAFE_STATUSES = frozenset({"success", "previewed", "sent", "healthy", "partial", "failed"})
_SAFE_PROFILES = frozenset({"conservative", "default", "aggressive"})


class CliArgumentError(ValueError):
    """Controlled signal for a rejected command line."""


class SafeParser(argparse.ArgumentParser):
    """Argument parser that never terminates the hosting process on errors."""

    def error(self, message: str) -> NoReturn:
        del message
        raise CliArgumentError("invalid_arguments")


class IntelligenceCliApplication(Protocol):
    async def analyze_pending(self, limit: int) -> dict[str, int | str]: ...

    async def backfill(self, limit: int) -> dict[str, int | str]: ...

    async def preview_report(self, report_date: date) -> dict[str, int | str]: ...

    async def send_report(self, report_date: date) -> dict[str, int | str]: ...

    async def test_send_report(self, report_date: date) -> dict[str, int | str]: ...

    async def resend_report(self, report_date: date) -> dict[str, int | str]: ...

    async def preview_alerts(self, since_hours: int) -> dict[str, int | str]: ...

    async def health(self) -> dict[str, int | str]: ...

    async def aclose(self) -> None: ...


def _batch_limit(value: str) -> int:
    if _POSITIVE_INTEGER.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("batch_limit_required")
    parsed = int(value)
    if parsed > MAX_BATCH_LIMIT:
        raise argparse.ArgumentTypeError("batch_limit_exceeded")
    return parsed


def _alert_preview_hours(value: str) -> int:
    if _POSITIVE_INTEGER.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("alert_preview_hours_required")
    parsed = int(value)
    if parsed > MAX_ALERT_PREVIEW_HOURS:
        raise argparse.ArgumentTypeError("alert_preview_hours_exceeded")
    return parsed


def _strict_date(value: str) -> date:
    if _ISO_DATE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("iso_date_required")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("valid_date_required") from None


def build_parser() -> argparse.ArgumentParser:
    parser = SafeParser(prog="python -m commerce_agent.intelligence_cli", add_help=True)
    commands = parser.add_subparsers(dest="command", required=True)

    analyze = commands.add_parser("analyze")
    mode = analyze.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pending", action="store_true")
    mode.add_argument("--backfill", action="store_true")
    analyze.add_argument("--limit", type=_batch_limit, default=10)

    report = commands.add_parser("report")
    report_commands = report.add_subparsers(dest="report_command", required=True)
    preview = report_commands.add_parser("preview")
    preview.add_argument("--date", type=_strict_date, required=True)
    for command in ("send", "test-send", "resend"):
        sending = report_commands.add_parser(command)
        sending.add_argument("--date", type=_strict_date, required=True)
        sending.add_argument("--confirm", action="store_true")

    alerts = commands.add_parser("alerts")
    alert_commands = alerts.add_subparsers(dest="alerts_command", required=True)
    alert_preview = alert_commands.add_parser("preview")
    alert_preview.add_argument("--since-hours", type=_alert_preview_hours, default=24)

    commands.add_parser("health")
    return parser


def _safe_result(result: dict[str, int | str]) -> dict[str, int | str]:
    rendered: dict[str, int | str] = {}
    for key in sorted(_SAFE_RESULT_KEYS.intersection(result)):
        value = result[key]
        if key == "status":
            if value not in _SAFE_STATUSES:
                raise ValueError("unsafe_status")
        elif key == "risk_profile":
            if value not in _SAFE_PROFILES:
                raise ValueError("unsafe_profile")
        elif not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("unsafe_count")
        rendered[key] = value
    if "status" not in rendered:
        raise ValueError("missing_status")
    return rendered


async def run_cli(
    argv: Sequence[str],
    app: IntelligenceCliApplication | None = None,
    output: TextIO | None = None,
) -> int:
    destination = output or sys.stdout
    try:
        args = build_parser().parse_args(argv)
    except (CliArgumentError, ValueError):
        destination.write("error=invalid_arguments\n")
        return 2

    if (
        args.command == "report"
        and args.report_command in {"send", "test-send", "resend"}
        and not args.confirm
    ):
        destination.write("error=confirm_required\n")
        return 2

    owned = app is None
    current: IntelligenceCliApplication | None = app
    safe: dict[str, int | str] | None = None
    failure_code: str | None = None
    failure_exit = 3
    try:
        if current is None:
            current = await build_application()
        if args.command == "analyze":
            result = (
                await current.analyze_pending(args.limit)
                if args.pending
                else await current.backfill(args.limit)
            )
        elif args.command == "report" and args.report_command == "preview":
            result = await current.preview_report(args.date)
        elif args.command == "report" and args.report_command == "send":
            result = await current.send_report(args.date)
        elif args.command == "report" and args.report_command == "test-send":
            result = await current.test_send_report(args.date)
        elif args.command == "report":
            result = await current.resend_report(args.date)
        elif args.command == "alerts":
            result = await current.preview_alerts(args.since_hours)
        else:
            result = await current.health()
        safe = _safe_result(result)
    except KeyError:
        failure_code = "target_not_found"
        failure_exit = 2
    except Exception as error:
        failure_code = controlled_cli_error(error)

    if owned and current is not None:
        try:
            await current.aclose()
        except Exception:
            if failure_code is None:
                failure_code = "runtime_error"
                failure_exit = 3

    if failure_code is not None:
        destination.write(f"error={failure_code}\n")
        return failure_exit
    if safe is None:
        destination.write("error=runtime_error\n")
        return 3
    destination.write(" ".join(f"{key}={value}" for key, value in safe.items()) + "\n")
    return 3 if safe["status"] in {"failed", "partial"} else 0


def controlled_cli_error(error: Exception) -> str:
    if isinstance(error, ReportAlreadySent):
        return "report_already_sent"
    if isinstance(error, ReportWindowOpen):
        return "report_window_open"
    if isinstance(error, InvalidModelOutput):
        return "invalid_model_output"
    if isinstance(error, TimeoutError):
        return "timeout"
    return "runtime_error"


class ProductionCliApplication:
    def __init__(
        self,
        runtime: IntelligenceRuntime,
        bindings: SqlAlchemyGroupBindingStore,
        database: Database,
        client: AsyncOpenAI,
        channel: FeishuChannel,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._runtime = runtime
        self._bindings = bindings
        self._database = database
        self._client = client
        self._channel = channel
        self._clock = clock

    async def _active_group(self) -> str:
        group_id = await self._bindings.get_active_chat_id()
        if group_id is None:
            raise KeyError("target_not_found")
        return group_id

    async def analyze_pending(self, limit: int) -> dict[str, int | str]:
        batch = await self._runtime.analysis.drain(limit=limit)
        return {
            "status": "success" if not batch.failed else "partial",
            "claimed": batch.claimed,
            "succeeded": batch.succeeded,
            "failed": batch.failed,
        }

    async def backfill(self, limit: int) -> dict[str, int | str]:
        created = await self._runtime.repository.backfill_jobs(limit=limit)
        return {"status": "success", "created": created}

    async def preview_report(self, report_date: date) -> dict[str, int | str]:
        group_id = await self._active_group()
        draft = await self._runtime.reports.preview(group_id, report_date)
        return {"status": "previewed", "selected": len(draft.selected_analysis_ids)}

    async def send_report(self, report_date: date) -> dict[str, int | str]:
        group_id = await self._active_group()
        outbox_id = await self._runtime.reports.queue_previewed(group_id, report_date)
        summary = await self._runtime.delivery.send_id(outbox_id)
        return {
            "status": "sent" if summary.sent else "partial",
            "sent": summary.sent,
            "failed": summary.failed,
            "skipped": summary.skipped,
        }

    async def test_send_report(self, report_date: date) -> dict[str, int | str]:
        return await self._send_report_variant(report_date, "test")

    async def resend_report(self, report_date: date) -> dict[str, int | str]:
        return await self._send_report_variant(report_date, "correction")

    async def _send_report_variant(
        self, report_date: date, variant: Literal["test", "correction"]
    ) -> dict[str, int | str]:
        group_id = await self._active_group()
        outbox_id = await self._runtime.reports.generate_variant_and_queue(
            group_id,
            report_date,
            variant=variant,
        )
        summary = await self._runtime.delivery.send_id(outbox_id)
        return {
            "status": "sent" if summary.sent else "partial",
            "sent": summary.sent,
            "failed": summary.failed,
            "skipped": summary.skipped,
        }

    async def preview_alerts(self, since_hours: int) -> dict[str, int | str]:
        group_id = await self._active_group()
        now = self._clock()
        analyses = await self._runtime.repository.list_unqueued_alert_candidates(
            since=now - timedelta(hours=since_hours), until=now
        )
        messages = await self._runtime.alerts.preview_batch(
            group_id,
            analyses,
            now=now,
        )
        items = tuple(
            item
            for message in messages
            for item in message.payload.get("items", [])
            if isinstance(item, dict)
        )
        return {
            "status": "previewed",
            "selected": len(items),
            "high": sum(item.get("risk_level") == "high" for item in items),
            "medium": sum(item.get("risk_level") == "medium" for item in items),
        }

    async def health(self) -> dict[str, int | str]:
        summary = await self._runtime.repository.health_summary(now=self._clock())
        group_id = await self._bindings.get_active_chat_id()
        profile = (
            self._runtime.default_profile
            if group_id is None
            else await self._runtime.preferences.get(
                group_id, default=self._runtime.default_profile
            )
        )
        return {**summary, "risk_profile": profile.value}

    async def aclose(self) -> None:
        try:
            await self._channel.disconnect()
        finally:
            try:
                await self._client.close()
            finally:
                await self._database.dispose()


async def build_application() -> ProductionCliApplication:
    settings = Settings()
    database = Database(settings.database_url)
    client: AsyncOpenAI | None = None
    channel: FeishuChannel | None = None
    try:
        await database.create_schema()
        client = AsyncOpenAI(
            api_key=settings.deepseek_api_key.get_secret_value(),
            base_url=str(settings.deepseek_base_url).rstrip("/"),
            timeout=settings.deepseek_timeout_seconds,
        )
        llm = DeepSeekGateway(client, settings.deepseek_model)
        channel = FeishuChannel(
            app_id=settings.lark_app_id,
            app_secret=settings.lark_app_secret.get_secret_value(),
            log_level=LogLevel.WARNING,
            security=SecurityConfig(mode="audit"),
        )
        bindings = SqlAlchemyGroupBindingStore(database.session)
        runtime = _build_intelligence(settings, database, llm, channel, bindings)
        return ProductionCliApplication(runtime, bindings, database, client, channel)
    except BaseException:
        await _cleanup_failed_build(channel, client, database)
        raise


async def _cleanup_failed_build(
    channel: FeishuChannel | None,
    client: AsyncOpenAI | None,
    database: Database,
) -> None:
    operations = (
        channel.disconnect if channel is not None else None,
        client.close if client is not None else None,
        database.dispose,
    )
    for operation in operations:
        if operation is None:
            continue
        try:
            await operation()
        except BaseException:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(run_cli(argv if argv is not None else sys.argv[1:]))
    except KeyboardInterrupt:
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
