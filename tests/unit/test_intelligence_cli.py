from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

import pytest

from commerce_agent.intelligence.models import RiskProfile
from commerce_agent.intelligence.reports import ReportWindowOpen
from commerce_agent.intelligence_cli import (
    MAX_ALERT_PREVIEW_HOURS,
    MAX_BATCH_LIMIT,
    ProductionCliApplication,
    build_application,
    build_parser,
    run_cli,
)


@dataclass
class FakeCliApplication:
    delivery_calls: int = 0
    closed: bool = False
    calls: list[tuple[str, object]] = field(default_factory=list)
    result: dict[str, int | str] | None = None
    failure: Exception | None = None

    async def _call(self, name: str, value: object = None) -> dict[str, int | str]:
        self.calls.append((name, value))
        if self.failure is not None:
            raise self.failure
        return self.result or {"status": "success", "succeeded": 1}

    async def analyze_pending(self, limit: int) -> dict[str, int | str]:
        return await self._call("analyze_pending", limit)

    async def backfill(self, limit: int) -> dict[str, int | str]:
        return await self._call("backfill", limit)

    async def preview_report(self, report_date: date) -> dict[str, int | str]:
        return await self._call("preview_report", report_date)

    async def send_report(self, report_date: date) -> dict[str, int | str]:
        self.delivery_calls += 1
        return await self._call("send_report", report_date)

    async def preview_alerts(self, since_hours: int) -> dict[str, int | str]:
        return await self._call("preview_alerts", since_hours)

    async def health(self) -> dict[str, int | str]:
        return await self._call("health")

    async def aclose(self) -> None:
        self.closed = True


async def invoke(arguments: list[str], app: FakeCliApplication | None = None) -> tuple[int, str]:
    output = io.StringIO()
    code = await run_cli(arguments, app=app or FakeCliApplication(), output=output)
    return code, output.getvalue()


def test_parser_exposes_only_the_documented_command_surface() -> None:
    parser = build_parser()

    assert parser.parse_args(["analyze", "--pending", "--limit", "2"]).pending is True
    assert parser.parse_args(["analyze", "--backfill", "--limit", "3"]).backfill is True
    assert parser.parse_args(["report", "preview", "--date", "2026-07-21"]).date == date(
        2026, 7, 21
    )
    assert (
        parser.parse_args(["report", "send", "--date", "2026-07-21", "--confirm"]).confirm is True
    )
    assert parser.parse_args(["alerts", "preview", "--since-hours", "24"]).since_hours == 24
    assert parser.parse_args(["health"]).command == "health"


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["unknown"],
        ["analyze"],
        ["analyze", "--pending", "--backfill"],
        ["analyze", "--pending", "--limit", "0"],
        ["analyze", "--pending", "--limit", "-1"],
        ["analyze", "--pending", "--limit", "+1"],
        ["report", "preview"],
        ["report", "preview", "--date", "20260721"],
        ["report", "preview", "--date", "2026-02-30"],
        ["alerts", "preview", "--since-hours", "0"],
        ["alerts", "preview", "--since-hours", "1.5"],
        ["health", "--verbose"],
    ],
)
async def test_invalid_arguments_exit_two_without_running_app(arguments: list[str]) -> None:
    app = FakeCliApplication()

    code, output = await invoke(arguments, app)

    assert code == 2
    assert output == "error=invalid_arguments\n"
    assert app.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["analyze", "--pending", "--limit", "101"],
        ["analyze", "--backfill", "--limit", "999999999999999999999999"],
        ["alerts", "preview", "--since-hours", "169"],
        ["alerts", "preview", "--since-hours", "999999999999999999999999"],
    ],
)
async def test_operational_bounds_reject_before_building_application(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from commerce_agent import intelligence_cli

    build_calls = 0

    async def forbidden_build():
        nonlocal build_calls
        build_calls += 1
        raise AssertionError("application must not be built")

    monkeypatch.setattr(intelligence_cli, "build_application", forbidden_build)
    output = io.StringIO()

    code = await run_cli(arguments, output=output)

    assert code == 2
    assert output.getvalue() == "error=invalid_arguments\n"
    assert build_calls == 0


async def test_operational_bounds_accept_documented_maximums() -> None:
    assert MAX_BATCH_LIMIT == 100
    assert MAX_ALERT_PREVIEW_HOURS == 168
    app = FakeCliApplication()

    analyze_code, _ = await invoke(["analyze", "--pending", "--limit", str(MAX_BATCH_LIMIT)], app)
    alerts_code, _ = await invoke(
        ["alerts", "preview", "--since-hours", str(MAX_ALERT_PREVIEW_HOURS)], app
    )

    assert analyze_code == alerts_code == 0
    assert app.calls == [
        ("analyze_pending", MAX_BATCH_LIMIT),
        ("preview_alerts", MAX_ALERT_PREVIEW_HOURS),
    ]


async def test_report_preview_never_sends() -> None:
    app = FakeCliApplication(result={"status": "previewed", "selected": 4})

    code, output = await invoke(["report", "preview", "--date", "2026-07-21"], app)

    assert code == 0
    assert app.delivery_calls == 0
    assert app.calls == [("preview_report", date(2026, 7, 21))]
    assert output == "selected=4 status=previewed\n"


async def test_report_send_requires_confirm_without_building_application() -> None:
    from commerce_agent import intelligence_cli

    build_calls = 0

    async def forbidden_build():
        nonlocal build_calls
        build_calls += 1
        raise AssertionError("application must not be built")

    output = io.StringIO()
    original = intelligence_cli.build_application
    intelligence_cli.build_application = forbidden_build
    try:
        code = await run_cli(["report", "send", "--date", "2026-07-21"], output=output)
    finally:
        intelligence_cli.build_application = original

    assert code == 2
    assert output.getvalue() == "error=confirm_required\n"
    assert build_calls == 0


async def test_confirmed_report_send_delivers_once() -> None:
    app = FakeCliApplication(result={"status": "sent", "sent": 1, "failed": 0})

    code, output = await invoke(["report", "send", "--date", "2026-07-21", "--confirm"], app)

    assert code == 0
    assert app.delivery_calls == 1
    assert output == "failed=0 sent=1 status=sent\n"


async def test_health_reports_profile_without_exposing_group_id() -> None:
    app = FakeCliApplication(
        result={
            "status": "healthy",
            "risk_profile": "default",
            "pending": 2,
            "group_id": "chat-one",
        }
    )

    code, output = await invoke(["health"], app)

    assert code == 0
    assert output == "pending=2 risk_profile=default status=healthy\n"
    assert "chat-one" not in output


async def test_cli_never_renders_content_secrets_or_urls_from_result() -> None:
    app = FakeCliApplication(
        result={
            "status": "success",
            "succeeded": 1,
            "body_text": "private article",
            "prompt": "system prompt",
            "model_output": "raw JSON",
            "question": "private question",
            "chat_id": "chat-one",
            "bind_code": "bind-secret",
            "api_key": "sk-secret",
            "url": "https://example.test/path?token=secret",
        }
    )

    code, output = await invoke(["analyze", "--pending", "--limit", "1"], app)

    assert code == 0
    assert output == "status=success succeeded=1\n"


@pytest.mark.parametrize("status", ["partial", "failed"])
async def test_partial_or_failed_result_exits_three(status: str) -> None:
    app = FakeCliApplication(result={"status": status, "failed": 1})

    code, output = await invoke(["analyze", "--pending"], app)

    assert code == 3
    assert output == f"failed=1 status={status}\n"


async def test_target_failure_is_safe_and_exits_two() -> None:
    app = FakeCliApplication(failure=KeyError("chat-one?token=secret"))

    code, output = await invoke(["report", "preview", "--date", "2026-07-21"], app)

    assert code == 2
    assert output == "error=target_not_found\n"
    assert "chat-one" not in output
    assert "token" not in output


async def test_open_report_window_returns_a_safe_cli_error() -> None:
    app = FakeCliApplication(failure=ReportWindowOpen("private report timing"))

    code, output = await invoke(
        ["report", "send", "--date", "2026-07-23", "--confirm"], app
    )

    assert code == 3
    assert output == "error=report_window_open\n"
    assert "private" not in output


@pytest.mark.parametrize(
    ("failure", "safe_code"),
    [
        (TimeoutError("https://example.test/?token=secret"), "timeout"),
        (RuntimeError("sk-secret private body"), "runtime_error"),
    ],
)
async def test_runtime_failure_is_controlled(failure: Exception, safe_code: str) -> None:
    app = FakeCliApplication(failure=failure)

    code, output = await invoke(["health"], app)

    assert code == 3
    assert output == f"error={safe_code}\n"
    assert "secret" not in output


async def test_injected_application_is_not_closed() -> None:
    app = FakeCliApplication()

    code, _ = await invoke(["health"], app)

    assert code == 0
    assert app.closed is False


async def test_production_health_uses_default_profile_when_no_group_is_bound() -> None:
    class Repository:
        async def health_summary(self, *, now):
            assert now.tzinfo is not None
            return {"status": "healthy", "analysis_pending": 0}

    class Bindings:
        async def get_active_chat_id(self):
            return None

    class Preferences:
        async def get(self, group_id, *, default):
            raise AssertionError(f"must not look up an unbound group: {group_id} {default}")

    app = ProductionCliApplication(
        SimpleNamespace(
            repository=Repository(),
            preferences=Preferences(),
            default_profile=RiskProfile.CONSERVATIVE,
        ),
        Bindings(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    result = await app.health()

    assert result == {
        "status": "healthy",
        "analysis_pending": 0,
        "risk_profile": "conservative",
    }


async def test_owned_application_close_failure_is_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from commerce_agent import intelligence_cli

    class CloseFailingApplication(FakeCliApplication):
        async def aclose(self) -> None:
            raise RuntimeError("close failed with sk-secret")

    app = CloseFailingApplication()

    async def factory():
        return app

    monkeypatch.setattr(intelligence_cli, "build_application", factory)
    output = io.StringIO()

    code = await run_cli(["health"], output=output)

    assert code == 3
    assert output.getvalue() == "error=runtime_error\n"
    assert "secret" not in output.getvalue()


@pytest.mark.parametrize(
    "failing_cleanup",
    [None, "channel", "client", "database", "all"],
)
async def test_build_failure_attempts_all_cleanup_and_preserves_original_error(
    failing_cleanup: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from commerce_agent import intelligence_cli

    cleanup_calls: list[str] = []
    startup_error = RuntimeError("startup_failed")

    class Secret:
        def get_secret_value(self) -> str:
            return "test-only-secret"

    settings = SimpleNamespace(
        database_url="sqlite+aiosqlite:///unused.db",
        deepseek_api_key=Secret(),
        deepseek_base_url="https://example.test",
        deepseek_timeout_seconds=1,
        deepseek_model="fake-model",
        lark_app_id="fake-app",
        lark_app_secret=Secret(),
    )

    def should_fail(name: str) -> bool:
        return failing_cleanup in {name, "all"}

    class FakeDatabase:
        session = object()

        def __init__(self, url: str) -> None:
            assert url == settings.database_url

        async def create_schema(self) -> None:
            pass

        async def dispose(self) -> None:
            cleanup_calls.append("database")
            if should_fail("database"):
                raise RuntimeError("database_cleanup_secret")

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["timeout"] == 1

        async def close(self) -> None:
            cleanup_calls.append("client")
            if should_fail("client"):
                raise RuntimeError("client_cleanup_secret")

    class FakeChannel:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["app_id"] == "fake-app"

        async def disconnect(self) -> None:
            cleanup_calls.append("channel")
            if should_fail("channel"):
                raise RuntimeError("channel_cleanup_secret")

    def fail_runtime(*args: object) -> object:
        assert args
        raise startup_error

    monkeypatch.setattr(intelligence_cli, "Settings", lambda: settings)
    monkeypatch.setattr(intelligence_cli, "Database", FakeDatabase)
    monkeypatch.setattr(intelligence_cli, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(intelligence_cli, "FeishuChannel", FakeChannel)
    monkeypatch.setattr(intelligence_cli, "_build_intelligence", fail_runtime)

    with pytest.raises(RuntimeError) as caught:
        await build_application()

    assert caught.value is startup_error
    assert cleanup_calls == ["channel", "client", "database"]
