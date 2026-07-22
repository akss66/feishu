from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from commerce_agent.ingestion.scheduler import IngestionScheduler
from commerce_agent.runtime import RuntimeResources, _serve


class AsyncCloser:
    def __init__(self, name: str, events: list[str]) -> None:
        self._name = name
        self._events = events

    async def aclose(self) -> None:
        self._events.append(self._name)


class ClientCloser:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def close(self) -> None:
        self._events.append("deepseek")


class DatabaseCloser:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def dispose(self) -> None:
        self._events.append("database")


class ChannelCloser:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def disconnect(self) -> None:
        self._events.append("channel")


class Adapter:
    def __init__(
        self,
        events: list[str],
        connect: Callable[[], object] | None = None,
    ) -> None:
        self._events = events
        self._connect = connect

    async def connect(self) -> None:
        self._events.append("connect")
        if self._connect is not None:
            result = self._connect()
            if asyncio.iscoroutine(result):
                await result

    async def close(self) -> None:
        self._events.append("adapter")


class Scheduler:
    def __init__(self, events: list[str], name: str = "scheduler") -> None:
        self._events = events
        self._name = name

    def start(self) -> None:
        self._events.append(f"{self._name}_start")

    async def aclose(self) -> None:
        self._events.append(f"{self._name}_stop")


def test_runtime_logging_suppresses_transport_queries_but_keeps_application_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from commerce_agent.runtime import _configure_logging

    canary = "https://public.example.com/items?canary=must-not-appear"
    with caplog.at_level(logging.INFO):
        _configure_logging("INFO")
        logging.getLogger("httpx").info("request %s", canary)
        logging.getLogger("httpcore.connection").info("connect %s", canary)
        logging.getLogger("lark_channel.ws").info("websocket %s", canary)
        logging.getLogger("commerce_agent.runtime").info("application-ready")

    assert "application-ready" in caplog.text
    assert canary not in caplog.text
    assert "?canary=" not in caplog.text
    for logger_name in ("httpx", "httpcore", "lark_channel"):
        assert logging.getLogger(logger_name).level == logging.WARNING


def resources(
    events: list[str],
    *,
    adapter: Adapter | None = None,
    scheduler: object | None = None,
    intelligence_scheduler: object | None = None,
) -> RuntimeResources:
    return RuntimeResources(
        database=DatabaseCloser(events),
        openai_client=ClientCloser(events),
        channel=ChannelCloser(events),
        adapter=adapter or Adapter(events),
        scheduler=scheduler or Scheduler(events),
        intelligence_scheduler=intelligence_scheduler,
        ingestion_resources=(
            AsyncCloser("http", events),
            AsyncCloser("browser", events),
        ),
    )


async def test_scheduler_starts_before_blocking_feishu_connection() -> None:
    events: list[str] = []
    connected = asyncio.Event()
    release = asyncio.Event()

    async def block_connect() -> None:
        connected.set()
        await release.wait()

    task = asyncio.create_task(
        _serve(resources(events, adapter=Adapter(events, block_connect)), scheduler_enabled=True)
    )
    await asyncio.wait_for(connected.wait(), timeout=1)
    assert events[:2] == ["scheduler_start", "connect"]

    release.set()
    await task


async def test_ingestion_failure_does_not_stop_feishu_connection() -> None:
    events: list[str] = []

    class FailingService:
        async def run_all(self, trigger: object) -> tuple[object, ...]:
            del trigger
            events.append("ingestion_failed")
            raise RuntimeError("source failed")

    class ImmediateBackend:
        def add_job(self, callback: Callable[[], object], **kwargs: object) -> None:
            del kwargs
            self.callback = callback

        def start(self) -> None:
            self.task = asyncio.create_task(self.callback())

        def shutdown(self, *, wait: bool) -> None:
            assert wait is True

    backend = ImmediateBackend()
    scheduler = IngestionScheduler(FailingService(), scheduler=backend)
    await _serve(resources(events, scheduler=scheduler), scheduler_enabled=True)
    await backend.task

    assert "ingestion_failed" in events
    assert "connect" in events


async def test_feishu_connection_failure_still_stops_scheduler() -> None:
    events: list[str] = []

    def fail_connect() -> None:
        raise RuntimeError("connection failed")

    with pytest.raises(RuntimeError, match="connection failed"):
        await _serve(
            resources(events, adapter=Adapter(events, fail_connect)),
            scheduler_enabled=True,
        )

    assert events[:4] == ["scheduler_start", "connect", "scheduler_stop", "adapter"]


async def test_all_resources_close_once_in_shutdown_order() -> None:
    events: list[str] = []

    await _serve(resources(events), scheduler_enabled=True)

    assert events == [
        "scheduler_start",
        "connect",
        "scheduler_stop",
        "adapter",
        "channel",
        "http",
        "browser",
        "deepseek",
        "database",
    ]


async def test_intelligence_scheduler_starts_first_and_stops_before_ingestion() -> None:
    events: list[str] = []

    await _serve(
        resources(
            events,
            intelligence_scheduler=Scheduler(events, "intelligence"),
        ),
        scheduler_enabled=True,
    )

    assert events == [
        "intelligence_start",
        "scheduler_start",
        "connect",
        "intelligence_stop",
        "scheduler_stop",
        "adapter",
        "channel",
        "http",
        "browser",
        "deepseek",
        "database",
    ]


def test_disabled_intelligence_graph_has_no_scheduler_or_qa() -> None:
    from commerce_agent.runtime import _build_intelligence

    settings = SimpleNamespace(
        intelligence_analysis_enabled=False,
        intelligence_daily_report_enabled=False,
        intelligence_alerts_enabled=False,
        intelligence_qa_enabled=False,
        intelligence_timezone="Asia/Shanghai",
        intelligence_daily_hour=9,
        intelligence_ai_concurrency=2,
        intelligence_risk_profile="default",
        intelligence_context_ttl_minutes=30,
        intelligence_qa_max_turns=6,
        deepseek_model="deepseek-v4-pro",
    )
    database = SimpleNamespace(session=object())

    runtime = _build_intelligence(
        settings,
        database,
        llm=object(),
        channel=object(),
        bindings=object(),
    )

    assert runtime.scheduler is None
    assert runtime.qa is None


def test_enabled_intelligence_graph_wires_qa_delivery_and_scheduler() -> None:
    from commerce_agent.runtime import _build_intelligence

    settings = SimpleNamespace(
        intelligence_analysis_enabled=True,
        intelligence_daily_report_enabled=True,
        intelligence_alerts_enabled=True,
        intelligence_qa_enabled=True,
        intelligence_timezone="Asia/Shanghai",
        intelligence_daily_hour=9,
        intelligence_ai_concurrency=2,
        intelligence_risk_profile="aggressive",
        intelligence_context_ttl_minutes=30,
        intelligence_qa_max_turns=6,
        deepseek_model="deepseek-v4-pro",
    )

    runtime = _build_intelligence(
        settings,
        SimpleNamespace(session=object()),
        llm=object(),
        channel=object(),
        bindings=object(),
    )

    assert runtime.scheduler is not None
    assert runtime.qa is not None
    assert runtime.delivery is not None
    assert runtime.default_profile.value == "aggressive"


async def test_disabled_scheduler_is_neither_started_nor_stopped() -> None:
    events: list[str] = []

    await _serve(resources(events), scheduler_enabled=False)

    assert "scheduler_start" not in events
    assert "scheduler_stop" not in events


async def test_runtime_rejects_browser_mode_before_creating_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from commerce_agent import runtime
    from commerce_agent.config import ProductionConfigurationError

    class BrowserSettings:
        ingestion_browser_enabled = True
        ingestion_scheduler_enabled = True

    def forbidden_database(url: str) -> object:
        del url
        raise AssertionError("database must not be created")

    async def forbidden_ingestion(settings: object, database: object) -> object:
        del settings, database
        raise AssertionError("ingestion resources must not be created")

    monkeypatch.setattr(runtime, "Database", forbidden_database)
    monkeypatch.setattr(runtime, "_build_ingestion", forbidden_ingestion)

    with pytest.raises(ProductionConfigurationError, match="browser ingestion is unavailable"):
        await runtime._run_configured(BrowserSettings())
