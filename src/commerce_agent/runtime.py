from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from lark_channel import FeishuChannel, LogLevel, SecurityConfig
from openai import AsyncOpenAI

from commerce_agent.application import BotService
from commerce_agent.config import Settings, require_browser_ingestion_disabled
from commerce_agent.integrations.deepseek import DeepSeekGateway
from commerce_agent.integrations.feishu import FeishuAdapter
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.group_bindings import SqlAlchemyGroupBindingStore

_TRANSPORT_LOGGERS = ("httpx", "httpcore", "lark_channel")


class _AsyncCloser(Protocol):
    async def aclose(self) -> None: ...


@dataclass(slots=True)
class RuntimeResources:
    database: Any | None = None
    openai_client: Any | None = None
    channel: Any | None = None
    adapter: Any | None = None
    scheduler: Any | None = None
    ingestion_resources: tuple[_AsyncCloser, ...] = field(default_factory=tuple)


async def run() -> None:
    settings = Settings()
    _configure_logging(settings.log_level)
    await _run_configured(settings)


def _configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for logger_name in _TRANSPORT_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


async def _run_configured(settings: Settings) -> None:
    require_browser_ingestion_disabled(bool(getattr(settings, "ingestion_browser_enabled", False)))
    scheduler_enabled = bool(getattr(settings, "ingestion_scheduler_enabled", False))
    resources = RuntimeResources()
    try:
        resources.database = Database(settings.database_url)
        await resources.database.create_schema()
        if scheduler_enabled:
            resources.scheduler, resources.ingestion_resources = await _build_ingestion(
                settings,
                resources.database,
            )

        bindings = SqlAlchemyGroupBindingStore(resources.database.session)
        resources.openai_client = AsyncOpenAI(
            api_key=settings.deepseek_api_key.get_secret_value(),
            base_url=str(settings.deepseek_base_url).rstrip("/"),
            timeout=settings.deepseek_timeout_seconds,
        )
        llm = DeepSeekGateway(resources.openai_client, settings.deepseek_model)
        service = BotService(bindings, llm, settings.bot_bind_code.get_secret_value())
        resources.channel = FeishuChannel(
            app_id=settings.lark_app_id,
            app_secret=settings.lark_app_secret.get_secret_value(),
            log_level=LogLevel.WARNING,
            security=SecurityConfig(mode="audit"),
        )
        resources.adapter = FeishuAdapter(resources.channel, service)
    except BaseException:
        await _close_resources(resources, scheduler_enabled=scheduler_enabled)
        raise

    await _serve(resources, scheduler_enabled=scheduler_enabled)


async def _build_ingestion(
    settings: Any,
    database: Any,
) -> tuple[Any, tuple[_AsyncCloser, ...]]:
    from commerce_agent.ingestion.collectors import (
        ApiCollector,
        BrowserCollector,
        FeedCollector,
        HtmlCollector,
        SitemapCollector,
    )
    from commerce_agent.ingestion.compliance import CompliancePolicy
    from commerce_agent.ingestion.extract import ContentExtractor, LinguaLanguageDetector
    from commerce_agent.ingestion.http import IngestionHttpClient
    from commerce_agent.ingestion.models import CollectorKind
    from commerce_agent.ingestion.scheduler import IngestionScheduler
    from commerce_agent.ingestion.security import UrlSafetyPolicy
    from commerce_agent.ingestion.service import IngestionService
    from commerce_agent.ingestion.snapshots import SnapshotStore
    from commerce_agent.ingestion_cli import build_registry
    from commerce_agent.persistence.ingestion import SqlAlchemyIngestionRepository

    registry = build_registry()
    repository = SqlAlchemyIngestionRepository(database.session)
    safety_policy = UrlSafetyPolicy()
    http_client = IngestionHttpClient(
        safety_policy=safety_policy,
        global_concurrency=settings.ingestion_global_concurrency,
        domain_rps=settings.ingestion_domain_rps,
        timeout_seconds=settings.ingestion_http_timeout_seconds,
        max_response_bytes=settings.ingestion_max_response_bytes,
        user_agent=settings.ingestion_user_agent,
    )
    try:
        collectors = {
            CollectorKind.RSS: FeedCollector(http_client),
            CollectorKind.SITEMAP: SitemapCollector(http_client),
            CollectorKind.HTML: HtmlCollector(http_client),
            CollectorKind.API: ApiCollector(http_client),
            CollectorKind.BROWSER: BrowserCollector(
                enabled=False,
                browser_port=None,
                timeout_seconds=settings.ingestion_http_timeout_seconds,
            ),
        }
        service = IngestionService(
            registry=registry,
            compliance=CompliancePolicy(),
            collectors=collectors,
            extractor=ContentExtractor(LinguaLanguageDetector()),
            snapshot_store=SnapshotStore(settings.snapshot_dir),
            repository=repository,
            max_concurrency=settings.ingestion_global_concurrency,
        )
        await service.initialize()
        scheduler = IngestionScheduler(
            service,
            interval_minutes=settings.ingestion_interval_minutes,
            timezone="UTC",
        )
        return scheduler, (http_client,)
    except BaseException:
        await http_client.aclose()
        raise


async def _serve(resources: RuntimeResources, *, scheduler_enabled: bool) -> None:
    try:
        if scheduler_enabled and resources.scheduler is not None:
            resources.scheduler.start()
        if resources.adapter is not None:
            await resources.adapter.connect()
    finally:
        await _close_resources(resources, scheduler_enabled=scheduler_enabled)


async def _close_resources(
    resources: RuntimeResources,
    *,
    scheduler_enabled: bool,
) -> None:
    first_error: BaseException | None = None

    async def close(operation: Any) -> None:
        nonlocal first_error
        try:
            await operation()
        except BaseException as error:
            if first_error is None:
                first_error = error

    if scheduler_enabled and resources.scheduler is not None:
        await close(resources.scheduler.aclose)
    if resources.adapter is not None:
        await close(resources.adapter.close)
    if resources.channel is not None:
        await close(resources.channel.disconnect)
    for resource in resources.ingestion_resources:
        await close(resource.aclose)
    if resources.openai_client is not None:
        await close(resources.openai_client.close)
    if resources.database is not None:
        await close(resources.database.dispose)
    if first_error is not None:
        raise first_error
