"""Administrator CLI for public-source ingestion."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, TextIO

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select

from commerce_agent.config import require_browser_ingestion_disabled
from commerce_agent.ingestion.bootstrap import AsyncCloser, build_resolver_bundle
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
from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    Platform,
    RunStatus,
    RunSummary,
    Trigger,
)
from commerce_agent.ingestion.registry import SourceRegistry
from commerce_agent.ingestion.service import IngestionService
from commerce_agent.ingestion.snapshots import SnapshotStore
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.ingestion import SqlAlchemyIngestionRepository
from commerce_agent.persistence.models import SourceHealth

_REGISTRY_PATH = Path(__file__).with_name("sources") / "public_sources.yaml"
_FAILED_RUN_STATUSES = frozenset({RunStatus.PARTIAL, RunStatus.FAILED})


class _IngestionSettings(BaseSettings):
    """Only settings needed by the ingestion administration process."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite+aiosqlite:///./commerce_agent.db"
    ingestion_global_concurrency: int = Field(default=4, gt=0)
    ingestion_domain_rps: float = Field(default=1.0, gt=0)
    ingestion_http_timeout_seconds: float = Field(default=20.0, gt=0)
    ingestion_max_response_bytes: int = Field(default=10_485_760, gt=0)
    ingestion_browser_enabled: bool = False
    ingestion_dns_mode: Literal["system", "cloudflare_doh"] = "system"
    snapshot_dir: Path = Path("./data/snapshots")
    ingestion_user_agent: str = Field(default="CrossBorderCommerceAgent/0.1", min_length=1)
    gdelt_original_fetch_enabled: bool = False
    gdelt_original_fetch_max_per_source: int = Field(default=5, ge=1, le=25)
    gdelt_media_body_retention_days: Literal[7] = 7


@dataclass(frozen=True, slots=True)
class HealthRow:
    source_id: str
    health_status: str = "unknown"
    consecutive_failures: int = 0
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    next_scheduled_at: datetime | None = None
    last_error_code: str | None = None


class CliApplication(Protocol):
    registry: SourceRegistry

    async def run_all(self) -> tuple[RunSummary, ...]: ...

    async def run_source(self, source_id: str) -> RunSummary: ...

    async def probe_source(self, source_id: str) -> RunSummary: ...

    async def health(self) -> tuple[HealthRow, ...]: ...

    async def aclose(self) -> None: ...


ApplicationFactory = Callable[[], Awaitable[CliApplication]]
RegistryFactory = Callable[[], SourceRegistry]


class _ProductionApplication:
    def __init__(
        self,
        *,
        registry: SourceRegistry,
        service: IngestionService,
        database: Database,
        http_client: IngestionHttpClient,
        resolver_resources: tuple[AsyncCloser, ...],
    ) -> None:
        self.registry = registry
        self._service = service
        self._database = database
        self._http_client = http_client
        self._resolver_resources = resolver_resources

    async def run_all(self) -> tuple[RunSummary, ...]:
        return await self._service.run_all(Trigger.MANUAL)

    async def run_source(self, source_id: str) -> RunSummary:
        return await self._service.run_source(source_id, Trigger.MANUAL)

    async def probe_source(self, source_id: str) -> RunSummary:
        return await self._service.probe_source(source_id)

    async def health(self) -> tuple[HealthRow, ...]:
        async with self._database.session() as session:
            stored = tuple(
                await session.scalars(select(SourceHealth).order_by(SourceHealth.source_id))
            )
        by_source = {row.source_id: row for row in stored}
        return tuple(
            _health_row(source.source_id, by_source.get(source.source_id))
            for source in self.registry.sources
        )

    async def aclose(self) -> None:
        await _close_application_resources(
            (self._http_client, *self._resolver_resources),
            self._database,
        )


class _ArgumentError(ValueError):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentError("invalid arguments")


async def build_application() -> CliApplication:
    """Build only public-ingestion dependencies and initialize their local schema."""

    settings = _IngestionSettings()
    require_browser_ingestion_disabled(settings.ingestion_browser_enabled)
    registry = build_registry()
    database = Database(settings.database_url)
    http_client: IngestionHttpClient | None = None
    resolver_resources: tuple[AsyncCloser, ...] = ()
    try:
        await database.create_schema()
        repository = SqlAlchemyIngestionRepository(database.session)
        resolver_bundle = build_resolver_bundle(settings.ingestion_dns_mode)
        resolver_resources = resolver_bundle.resources
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
        return _ProductionApplication(
            registry=registry,
            service=service,
            database=database,
            http_client=http_client,
            resolver_resources=resolver_resources,
        )
    except BaseException:
        try:
            resources: tuple[AsyncCloser, ...] = resolver_resources
            if http_client is not None:
                resources = (http_client, *resources)
            await _close_application_resources(resources, database)
        except BaseException:
            pass
        raise


async def _close_application_resources(
    resources: tuple[AsyncCloser, ...],
    database: Database,
) -> None:
    first_error: BaseException | None = None
    for resource in resources:
        try:
            await resource.aclose()
        except BaseException as error:
            if first_error is None:
                first_error = error
    try:
        await database.dispose()
    except BaseException as error:
        if first_error is None:
            first_error = error
    if first_error is not None:
        raise first_error


def build_registry() -> SourceRegistry:
    """Load the source registry without initializing runtime dependencies."""

    return SourceRegistry.from_yaml(_REGISTRY_PATH)


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="commerce-agent-ingestion")
    commands = parser.add_subparsers(dest="command", required=True)

    sources_parser = commands.add_parser("sources")
    source_commands = sources_parser.add_subparsers(dest="sources_command", required=True)
    source_commands.add_parser("list")
    source_commands.add_parser("coverage")

    run_parser = commands.add_parser("run")
    target = run_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", dest="run_all")
    target.add_argument("--source", metavar="SOURCE_ID")

    probe_parser = commands.add_parser("probe")
    probe_parser.add_argument("--source", metavar="SOURCE_ID", required=True)

    commands.add_parser("health")
    return parser


async def run_cli(
    argv: Sequence[str] | None = None,
    *,
    app_factory: ApplicationFactory = build_application,
    registry_factory: RegistryFactory = build_registry,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = build_parser().parse_args(argv)
    except _ArgumentError:
        errors.write("error: invalid arguments\n")
        return 2
    except SystemExit as exc:
        return int(exc.code or 0)

    source_id = None
    if arguments.command == "probe":
        source_id = arguments.source
    elif arguments.command == "run" and not arguments.run_all:
        source_id = arguments.source
    if source_id is not None:
        try:
            registry_factory().require(source_id)
        except KeyError:
            errors.write("error: unknown source_id\n")
            return 2
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                raise
            errors.write("error: command failed\n")
            return 3

    if arguments.command == "sources":
        try:
            registry = registry_factory()
            if arguments.sources_command == "coverage":
                _write_coverage(registry, output)
            else:
                _write_sources(registry, output)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                raise
            errors.write("error: command failed\n")
            return 3
        return 0

    application: CliApplication | None = None
    exit_code = 0
    try:
        application = await app_factory()
        if arguments.command == "health":
            _write_health(await application.health(), output)
        else:
            summaries: tuple[RunSummary, ...]
            if arguments.command == "probe":
                summaries = (await application.probe_source(arguments.source),)
            elif arguments.run_all:
                summaries = await application.run_all()
            else:
                summaries = (await application.run_source(arguments.source),)
            _write_runs(summaries, output)
            if any(
                item.status in _FAILED_RUN_STATUSES or item.error_code == "source_already_running"
                for item in summaries
            ):
                exit_code = 3
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
            raise
        errors.write("error: command failed\n")
        exit_code = 3
    finally:
        if application is not None:
            try:
                await application.aclose()
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                    raise
                errors.write("error: command failed\n")
                exit_code = 3
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run_cli(argv))


def _write_sources(registry: SourceRegistry, output: TextIO) -> None:
    coverage = registry.platform_coverage()
    rows = []
    for source in registry.sources:
        platforms = ",".join(platform.value for platform in source.platforms)
        statuses = ",".join(coverage[platform].value for platform in source.platforms)
        rows.append(
            (
                source.source_id,
                platforms,
                source.trust_tier.value,
                source.compliance.value,
                "yes" if source.enabled else "no",
                source.collector.value,
                statuses,
            )
        )
    _write_table(
        ("SOURCE", "PLATFORM", "TRUST", "COMPLIANCE", "ENABLED", "COLLECTOR", "COVERAGE"),
        rows,
        output,
    )


def _write_coverage(registry: SourceRegistry, output: TextIO) -> None:
    coverage = registry.platform_coverage()
    rows: list[tuple[str, ...]] = []
    for platform in Platform:
        sources = tuple(source for source in registry.sources if platform in source.platforms)
        rows.append(
            (
                platform.value,
                str(sum(source.enabled for source in sources)),
                str(sum(source.compliance is ComplianceStatus.ALLOWED for source in sources)),
                str(
                    sum(
                        source.compliance is ComplianceStatus.AUTHORIZATION_REQUIRED
                        for source in sources
                    )
                ),
                str(
                    sum(source.compliance is ComplianceStatus.PENDING_REVIEW for source in sources)
                ),
                str(sum(source.compliance is ComplianceStatus.DENIED for source in sources)),
                str(len(sources)),
                coverage[platform].value,
            )
        )
    _write_table(
        ("PLATFORM", "ENABLED", "ALLOWED", "AUTH", "PENDING", "DENIED", "TOTAL", "STATUS"),
        rows,
        output,
    )


def _write_runs(summaries: Sequence[RunSummary], output: TextIO) -> None:
    rows = [
        (
            item.source_id,
            item.status.value,
            str(item.discovered),
            str(item.created),
            str(item.updated),
            str(item.skipped),
            str(item.failed),
            str(item.http_requests),
            str(item.http_not_modified),
            str(item.bytes_received),
            item.error_code or "-",
        )
        for item in sorted(summaries, key=lambda summary: summary.source_id)
    ]
    _write_table(
        (
            "SOURCE",
            "STATUS",
            "FOUND",
            "CREATED",
            "UPDATED",
            "SKIPPED",
            "FAILED",
            "HTTP",
            "NOT_MODIFIED",
            "BYTES",
            "ERROR",
        ),
        rows,
        output,
    )


def _write_health(rows: Sequence[HealthRow], output: TextIO) -> None:
    values = [
        (
            row.source_id,
            row.health_status,
            str(row.consecutive_failures),
            _timestamp(row.last_attempt_at),
            _timestamp(row.last_success_at),
            _timestamp(row.next_scheduled_at),
            row.last_error_code or "-",
        )
        for row in sorted(rows, key=lambda item: item.source_id)
    ]
    _write_table(
        ("SOURCE", "HEALTH", "FAILURES", "LAST_ATTEMPT", "LAST_SUCCESS", "NEXT", "ERROR"),
        values,
        output,
    )


def _write_table(headers: tuple[str, ...], rows: Sequence[tuple[str, ...]], output: TextIO) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def render(row: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip()

    output.write(render(headers) + "\n")
    output.write(render(tuple("-" * width for width in widths)) + "\n")
    for row in rows:
        output.write(render(row) + "\n")


def _timestamp(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _health_row(source_id: str, stored: SourceHealth | None) -> HealthRow:
    if stored is None:
        return HealthRow(source_id=source_id)
    return HealthRow(
        source_id=source_id,
        health_status=stored.health_status,
        consecutive_failures=stored.consecutive_failures,
        last_attempt_at=stored.last_attempt_at,
        last_success_at=stored.last_success_at,
        next_scheduled_at=stored.next_scheduled_at,
        last_error_code=stored.last_error_code,
    )


if __name__ == "__main__":  # pragma: no cover - exercised through the module entry point
    raise SystemExit(main())
