from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

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
    intelligence_scheduler: Any | None = None
    email_notice_scheduler: Any | None = None
    ingestion_resources: tuple[_AsyncCloser, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class IntelligenceRuntime:
    scheduler: Any | None
    repository: Any
    analysis: Any
    reports: Any
    alerts: Any
    preferences: Any
    default_profile: Any
    qa: Any | None
    delivery: Any


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
        manual_submissions = _build_manual_submissions(resources.database)
        if bool(getattr(settings, "official_notice_email_enabled", False)):
            resources.email_notice_scheduler = _build_email_notice_scheduler(
                settings,
                manual_submissions,
            )
        resources.openai_client = AsyncOpenAI(
            api_key=settings.deepseek_api_key.get_secret_value(),
            base_url=str(settings.deepseek_base_url).rstrip("/"),
            timeout=settings.deepseek_timeout_seconds,
        )
        llm = DeepSeekGateway(resources.openai_client, settings.deepseek_model)
        resources.channel = FeishuChannel(
            app_id=settings.lark_app_id,
            app_secret=settings.lark_app_secret.get_secret_value(),
            log_level=LogLevel.WARNING,
            security=SecurityConfig(mode="audit"),
        )
        intelligence_enabled = any(
            bool(getattr(settings, name, False))
            for name in (
                "intelligence_analysis_enabled",
                "intelligence_daily_report_enabled",
                "intelligence_alerts_enabled",
                "intelligence_qa_enabled",
            )
        )
        if intelligence_enabled:
            intelligence = _build_intelligence(
                settings,
                resources.database,
                llm,
                resources.channel,
                bindings,
                ingestion=(
                    resources.scheduler.service if resources.scheduler is not None else None
                ),
            )
            resources.intelligence_scheduler = intelligence.scheduler
            service = BotService(
                bindings,
                llm,
                settings.bot_bind_code.get_secret_value(),
                risk_profiles=intelligence.preferences,
                default_risk_profile=intelligence.default_profile,
                qa=intelligence.qa,
                manual_submissions=manual_submissions,
            )
            resources.adapter = FeishuAdapter(
                resources.channel,
                service,
                delivery=intelligence.delivery,
                qa_concurrency=settings.intelligence_ai_concurrency,
            )
        else:
            service = BotService(
                bindings,
                llm,
                settings.bot_bind_code.get_secret_value(),
                manual_submissions=manual_submissions,
            )
            resources.adapter = FeishuAdapter(resources.channel, service)
    except BaseException:
        await _close_resources(resources, scheduler_enabled=scheduler_enabled)
        raise

    await _serve(resources, scheduler_enabled=scheduler_enabled)


def _build_manual_submissions(database: Any) -> Any:
    from commerce_agent.ingestion.manual_submissions import ManualSubmissionService
    from commerce_agent.ingestion.official_notices import OfficialAccountRegistry
    from commerce_agent.persistence.ingestion import SqlAlchemyIngestionRepository

    accounts_path = Path(__file__).parent / "sources" / "official_accounts.yaml"
    accounts = OfficialAccountRegistry.from_yaml(accounts_path)
    return ManualSubmissionService(
        accounts,
        SqlAlchemyIngestionRepository(database.session),
    )


def _build_email_notice_scheduler(settings: Any, sink: Any) -> Any:
    from commerce_agent.ingestion.email_notices import (
        EmailNoticeIngestionService,
        ImapOfficialNoticeProvider,
        StdlibImapClient,
        parse_allowed_senders,
    )
    from commerce_agent.ingestion.official_notices import OfficialAccountRegistry
    from commerce_agent.ingestion.scheduler import IngestionScheduler

    accounts = OfficialAccountRegistry.from_yaml(
        Path(__file__).parent / "sources" / "official_accounts.yaml"
    )
    client = StdlibImapClient(
        host=settings.official_notice_email_host,
        port=settings.official_notice_email_port,
        username=settings.official_notice_email_username,
        password=settings.official_notice_email_password.get_secret_value(),
        folder=settings.official_notice_email_folder,
    )
    provider = ImapOfficialNoticeProvider(
        client,
        accounts=accounts,
        allowed_senders=parse_allowed_senders(settings.official_notice_email_allowed_senders),
        max_message_bytes=settings.official_notice_email_max_message_bytes,
        max_attachment_bytes=settings.official_notice_email_max_attachment_bytes,
    )
    return IngestionScheduler(
        EmailNoticeIngestionService(provider, sink),
        interval_minutes=settings.ingestion_interval_minutes,
        retention_enabled=False,
        timezone="UTC",
    )


def _build_intelligence(
    settings: Any,
    database: Any,
    llm: Any,
    channel: Any,
    bindings: Any,
    ingestion: Any | None = None,
) -> IntelligenceRuntime:
    from commerce_agent.ingestion.pre_report import PreReportPipeline
    from commerce_agent.intelligence.analyzer import IntelligenceAnalyzer
    from commerce_agent.intelligence.delivery import (
        DeliveryWorker,
        FeishuDeliveryPort,
        FeishuMessageRenderer,
    )
    from commerce_agent.intelligence.evidence import EvidenceScorer
    from commerce_agent.intelligence.models import RiskProfile
    from commerce_agent.intelligence.qa import QaService, ThreadContextStore
    from commerce_agent.intelligence.reports import (
        AlertComposer,
        DailyReportComposer,
        DailyReportService,
    )
    from commerce_agent.intelligence.repository import SqlAlchemyIntelligenceRepository
    from commerce_agent.intelligence.retrieval import CorpusRetriever
    from commerce_agent.intelligence.risk import RiskPolicy
    from commerce_agent.intelligence.scheduler import IntelligenceScheduler
    from commerce_agent.intelligence.service import AnalysisService
    from commerce_agent.persistence.intelligence_preferences import (
        SqlAlchemyIntelligencePreferenceStore,
    )

    repository = SqlAlchemyIntelligenceRepository(database.session)
    preferences = SqlAlchemyIntelligencePreferenceStore(database.session)
    risk_policy = RiskPolicy()
    default_profile = RiskProfile(settings.intelligence_risk_profile)
    timezone = ZoneInfo(settings.intelligence_timezone)
    analysis = AnalysisService(
        repository,
        IntelligenceAnalyzer(llm),
        EvidenceScorer(),
        risk_policy,
        concurrency=settings.intelligence_ai_concurrency,
        model_name=settings.deepseek_model,
    )
    reports = DailyReportService(
        repository,
        DailyReportComposer(timezone),
        preferences,
        timezone=timezone,
        default_profile=default_profile,
    )
    alerts = AlertComposer(
        repository,
        preferences,
        risk_policy,
        default_profile=default_profile,
    )
    delivery = DeliveryWorker(
        repository,
        FeishuDeliveryPort(channel, FeishuMessageRenderer()),
        bindings=bindings,
    )
    qa = (
        QaService(
            CorpusRetriever(repository),
            llm,
            repository,
            ThreadContextStore(
                max_turns=settings.intelligence_qa_max_turns,
                ttl=timedelta(minutes=settings.intelligence_context_ttl_minutes),
            ),
        )
        if settings.intelligence_qa_enabled
        else None
    )
    any_enabled = any(
        (
            settings.intelligence_analysis_enabled,
            settings.intelligence_daily_report_enabled,
            settings.intelligence_alerts_enabled,
            settings.intelligence_qa_enabled,
        )
    )
    scheduler = (
        IntelligenceScheduler(
            analysis=analysis,
            reports=reports,
            pre_report=PreReportPipeline(
                ingestion,
                analysis,
                reports,
                timezone=timezone,
            ),
            alerts=alerts,
            delivery=delivery,
            bindings=bindings,
            analysis_enabled=settings.intelligence_analysis_enabled,
            alerts_enabled=settings.intelligence_alerts_enabled,
            daily_enabled=settings.intelligence_daily_report_enabled,
            delivery_enabled=any(
                (
                    settings.intelligence_daily_report_enabled,
                    settings.intelligence_alerts_enabled,
                    settings.intelligence_qa_enabled,
                )
            ),
            daily_hour=settings.intelligence_daily_hour,
            timezone=settings.intelligence_timezone,
        )
        if any_enabled
        else None
    )
    return IntelligenceRuntime(
        scheduler=scheduler,
        repository=repository,
        analysis=analysis,
        reports=reports,
        alerts=alerts,
        preferences=preferences,
        default_profile=default_profile,
        qa=qa,
        delivery=delivery,
    )


async def _build_ingestion(
    settings: Any,
    database: Any,
) -> tuple[Any, tuple[_AsyncCloser, ...]]:
    from commerce_agent.ingestion.bootstrap import build_resolver_bundle
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
    from commerce_agent.ingestion.service import IngestionService
    from commerce_agent.ingestion.snapshots import SnapshotStore
    from commerce_agent.ingestion_cli import build_registry
    from commerce_agent.persistence.ingestion import SqlAlchemyIngestionRepository

    registry = build_registry()
    repository = SqlAlchemyIngestionRepository(database.session)
    resolver_bundle = build_resolver_bundle(settings.ingestion_dns_mode)
    http_client: IngestionHttpClient | None = None
    try:
        http_client = IngestionHttpClient(
            safety_policy=resolver_bundle.safety_policy,
            global_concurrency=settings.ingestion_global_concurrency,
            domain_rps=settings.ingestion_domain_rps,
            timeout_seconds=settings.ingestion_http_timeout_seconds,
            max_response_bytes=settings.ingestion_max_response_bytes,
            user_agent=settings.ingestion_user_agent,
            max_redirects=3,
        )
        collectors = {
            CollectorKind.RSS: FeedCollector(http_client),
            CollectorKind.SITEMAP: SitemapCollector(http_client),
            CollectorKind.HTML: HtmlCollector(http_client),
            CollectorKind.API: ApiCollector(
                http_client,
                fetch_gdelt_originals=settings.gdelt_original_fetch_enabled,
                gdelt_original_fetch_limit=settings.gdelt_original_fetch_max_per_source,
            ),
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
            gdelt_media_body_retention_days=settings.gdelt_media_body_retention_days,
        )
        await service.initialize()
        scheduler = IngestionScheduler(
            service,
            interval_minutes=settings.ingestion_interval_minutes,
            timezone="UTC",
        )
        return scheduler, (http_client, *resolver_bundle.resources)
    except BaseException:
        resources: tuple[Any, ...] = resolver_bundle.resources
        if http_client is not None:
            resources = (http_client, *resources)
        for resource in resources:
            try:
                await resource.aclose()
            except BaseException:
                pass
        raise


async def _serve(resources: RuntimeResources, *, scheduler_enabled: bool) -> None:
    try:
        if resources.intelligence_scheduler is not None:
            resources.intelligence_scheduler.start()
        if resources.email_notice_scheduler is not None:
            resources.email_notice_scheduler.start()
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

    if resources.intelligence_scheduler is not None:
        await close(resources.intelligence_scheduler.aclose)
    if resources.email_notice_scheduler is not None:
        await close(resources.email_notice_scheduler.aclose)
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
