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
    email_notice_scheduler: object | None = None,
) -> RuntimeResources:
    return RuntimeResources(
        database=DatabaseCloser(events),
        openai_client=ClientCloser(events),
        channel=ChannelCloser(events),
        adapter=adapter or Adapter(events),
        scheduler=scheduler or Scheduler(events),
        intelligence_scheduler=intelligence_scheduler,
        email_notice_scheduler=email_notice_scheduler,
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


async def test_optional_email_notice_scheduler_has_independent_lifecycle() -> None:
    events: list[str] = []

    await _serve(
        resources(
            events,
            email_notice_scheduler=Scheduler(events, "email"),
        ),
        scheduler_enabled=True,
    )

    assert events == [
        "email_start",
        "scheduler_start",
        "connect",
        "email_stop",
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


async def test_runtime_passes_configured_concurrency_to_qa_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from commerce_agent import runtime

    captured: dict[str, object] = {}

    class Secret:
        def get_secret_value(self) -> str:
            return "test-only"

    settings = SimpleNamespace(
        ingestion_browser_enabled=False,
        ingestion_scheduler_enabled=False,
        database_url="sqlite+aiosqlite:///:memory:",
        deepseek_api_key=Secret(),
        deepseek_base_url="https://example.invalid",
        deepseek_timeout_seconds=1,
        deepseek_model="test-model",
        lark_app_id="test-app",
        lark_app_secret=Secret(),
        bot_bind_code=Secret(),
        intelligence_analysis_enabled=False,
        intelligence_daily_report_enabled=False,
        intelligence_alerts_enabled=False,
        intelligence_qa_enabled=True,
        intelligence_ai_concurrency=7,
    )

    class Database:
        session = object()

        def __init__(self, url: str) -> None:
            del url

        async def create_schema(self) -> None: ...

    class Adapter:
        def __init__(
            self,
            channel: object,
            service: object,
            delivery: object,
            *,
            qa_concurrency: int,
        ) -> None:
            del channel, service, delivery
            captured["qa_concurrency"] = qa_concurrency

    async def serve(resources: object, *, scheduler_enabled: bool) -> None:
        del resources, scheduler_enabled

    intelligence = SimpleNamespace(
        scheduler=None,
        preferences=object(),
        default_profile=object(),
        qa=object(),
        delivery=object(),
    )
    monkeypatch.setattr(runtime, "Database", Database)
    monkeypatch.setattr(runtime, "AsyncOpenAI", lambda **kwargs: object())
    monkeypatch.setattr(runtime, "SqlAlchemyGroupBindingStore", lambda session: object())
    monkeypatch.setattr(runtime, "DeepSeekGateway", lambda client, model: object())
    monkeypatch.setattr(runtime, "FeishuChannel", lambda **kwargs: object())
    monkeypatch.setattr(
        runtime,
        "_build_intelligence",
        lambda *args, **kwargs: intelligence,
    )
    monkeypatch.setattr(runtime, "BotService", lambda *args, **kwargs: object())
    monkeypatch.setattr(runtime, "FeishuAdapter", Adapter)
    monkeypatch.setattr(runtime, "_serve", serve)

    await runtime._run_configured(settings)

    assert captured == {"qa_concurrency": 7}


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


@pytest.mark.parametrize("fail_initialization", [False, True])
async def test_runtime_ingestion_uses_shared_resolver_and_owns_its_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    fail_initialization: bool,
) -> None:
    from commerce_agent import ingestion_cli, runtime
    from commerce_agent.ingestion import bootstrap, collectors, extract, http, scheduler, service

    events: list[str] = []
    captured: dict[str, object] = {}

    class Closer:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self) -> None:
            events.append(self.name)

    resolver = Closer("resolver")
    policy = object()

    def build_bundle(mode: str) -> object:
        captured["mode"] = mode
        return SimpleNamespace(safety_policy=policy, resources=(resolver,))

    class HttpClient(Closer):
        def __init__(self, **kwargs: object) -> None:
            super().__init__("http")
            captured["http_kwargs"] = kwargs

    class Collector:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

    class ApiCollector:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args
            captured["api_collector_kwargs"] = kwargs

    class Extractor:
        def __init__(self, detector: object) -> None:
            del detector

    class Repository:
        def __init__(self, session: object) -> None:
            del session

    class Service:
        def __init__(self, **kwargs: object) -> None:
            captured["service_kwargs"] = kwargs

        async def initialize(self) -> None:
            events.append("initialize")
            if fail_initialization:
                raise RuntimeError("initialization failed")

    class IngestionScheduler:
        def __init__(self, service: object, **kwargs: object) -> None:
            captured["scheduler"] = (service, kwargs)

    settings = SimpleNamespace(
        ingestion_dns_mode="cloudflare_doh",
        ingestion_global_concurrency=2,
        ingestion_domain_rps=1.0,
        ingestion_http_timeout_seconds=3.0,
        ingestion_max_response_bytes=4096,
        ingestion_user_agent="test-agent",
        ingestion_interval_minutes=120,
        gdelt_original_fetch_enabled=False,
        gdelt_original_fetch_max_per_source=5,
        gdelt_media_body_retention_days=7,
        snapshot_dir=".",
    )
    monkeypatch.setattr(bootstrap, "build_resolver_bundle", build_bundle)
    monkeypatch.setattr(ingestion_cli, "build_registry", lambda: SimpleNamespace())
    monkeypatch.setattr(http, "IngestionHttpClient", HttpClient)
    monkeypatch.setattr(collectors, "ApiCollector", ApiCollector)
    collector_names = ("BrowserCollector", "FeedCollector", "HtmlCollector", "SitemapCollector")
    for name in collector_names:
        monkeypatch.setattr(collectors, name, Collector)
    monkeypatch.setattr(extract, "ContentExtractor", Extractor)
    monkeypatch.setattr(extract, "LinguaLanguageDetector", object)
    monkeypatch.setattr(service, "IngestionService", Service)
    monkeypatch.setattr(scheduler, "IngestionScheduler", IngestionScheduler)
    monkeypatch.setattr(
        "commerce_agent.persistence.ingestion.SqlAlchemyIngestionRepository",
        Repository,
    )

    if fail_initialization:
        with pytest.raises(RuntimeError, match="initialization failed"):
            await runtime._build_ingestion(settings, SimpleNamespace(session=object()))
        assert events == ["initialize", "http", "resolver"]
        return

    built_scheduler, owned_resources = await runtime._build_ingestion(
        settings,
        SimpleNamespace(session=object()),
    )

    assert isinstance(built_scheduler, IngestionScheduler)
    assert captured["mode"] == "cloudflare_doh"
    assert captured["http_kwargs"]["safety_policy"] is policy  # type: ignore[index]
    assert captured["http_kwargs"]["max_redirects"] == 3  # type: ignore[index]
    assert captured["api_collector_kwargs"] == {  # type: ignore[comparison-overlap]
        "fetch_gdelt_originals": False,
        "gdelt_original_fetch_limit": 5,
    }
    assert captured["service_kwargs"]["gdelt_media_body_retention_days"] == 7  # type: ignore[index]
    assert owned_resources[0].name == "http"  # type: ignore[attr-defined]
    assert owned_resources[1] is resolver
