from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    Platform,
    RunStatus,
    RunSummary,
    SourceDefinition,
    Trigger,
    TrustTier,
)
from commerce_agent.ingestion.registry import SourceRegistry
from commerce_agent.ingestion_cli import (
    HealthRow,
    _IngestionSettings,
    _ProductionApplication,
    build_application,
    run_cli,
)

NOW = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)


def source(
    source_id: str,
    *,
    platform: str,
    trust_tier: TrustTier = TrustTier.OFFICIAL,
    compliance: ComplianceStatus = ComplianceStatus.ALLOWED,
    enabled: bool = True,
    collector: CollectorKind = CollectorKind.RSS,
) -> SourceDefinition:
    from commerce_agent.ingestion.models import Platform

    return SourceDefinition(
        source_id=source_id,
        name=source_id,
        entry_url=f"https://example.com/{source_id}",
        platforms=(Platform(platform),),
        trust_tier=trust_tier,
        collector=collector,
        compliance=compliance,
        enabled=enabled,
        regions=("global",),
        language_hint="en",
        interval_minutes=120,
        terms_url="https://example.com/terms",
        robots_url="https://example.com/robots.txt",
        reviewed_at=date(2026, 7, 20),
        compliance_notes="public",
    )


def summary(source_id: str, status: RunStatus, *, error_code: str | None = None) -> RunSummary:
    return RunSummary(
        source_id=source_id,
        trigger=Trigger.MANUAL,
        status=status,
        started_at=NOW,
        finished_at=NOW,
        discovered=2,
        created=1,
        skipped=1,
        failed=1 if status in {RunStatus.PARTIAL, RunStatus.FAILED} else 0,
        error_code=error_code,
        http_requests=3,
        http_not_modified=1,
        bytes_received=42,
        error_summary=error_code,
    )


@dataclass
class FakeApplication:
    registry: SourceRegistry
    all_summaries: Sequence[RunSummary] = ()
    source_summaries: dict[str, RunSummary] = field(default_factory=dict)
    health_rows: Sequence[HealthRow] = ()
    run_all_calls: int = 0
    run_source_calls: list[str] = field(default_factory=list)
    probe_source_calls: list[str] = field(default_factory=list)
    closed: bool = False

    async def run_all(self) -> tuple[RunSummary, ...]:
        self.run_all_calls += 1
        return tuple(self.all_summaries)

    async def run_source(self, source_id: str) -> RunSummary:
        self.run_source_calls.append(source_id)
        return self.source_summaries[source_id]

    async def probe_source(self, source_id: str) -> RunSummary:
        self.probe_source_calls.append(source_id)
        return self.source_summaries[source_id]

    async def health(self) -> tuple[HealthRow, ...]:
        return tuple(self.health_rows)

    async def aclose(self) -> None:
        self.closed = True


def factory_for(app: FakeApplication, calls: list[str] | None = None):
    async def factory() -> FakeApplication:
        if calls is not None:
            calls.append("built")
        return app

    return factory


async def invoke(app: FakeApplication, *arguments: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = await run_cli(
        list(arguments),
        app_factory=factory_for(app),
        registry_factory=lambda: app.registry,
        stdout=stdout,
        stderr=stderr,
    )
    return exit_code, stdout.getvalue(), stderr.getvalue()


async def test_sources_list_is_deterministic_and_includes_coverage_metadata() -> None:
    registry = SourceRegistry(
        [
            source(
                "z-media",
                platform="temu",
                trust_tier=TrustTier.MEDIA,
                compliance=ComplianceStatus.ALLOWED,
                collector=CollectorKind.HTML,
            ),
            source(
                "a-official",
                platform="amazon",
                compliance=ComplianceStatus.AUTHORIZATION_REQUIRED,
                enabled=False,
                collector=CollectorKind.BROWSER,
            ),
        ]
    )
    app = FakeApplication(registry)

    exit_code, stdout, stderr = await invoke(app, "sources", "list")

    assert exit_code == 0
    assert stderr == ""
    assert stdout.index("a-official") < stdout.index("z-media")
    assert "SOURCE" in stdout
    assert "PLATFORM" in stdout
    assert "TRUST" in stdout
    assert "COMPLIANCE" in stdout
    assert "ENABLED" in stdout
    assert "COLLECTOR" in stdout
    assert "COVERAGE" in stdout
    assert "authorization_required" in stdout
    assert "public_covered_seller_center_pending" in stdout
    assert "partial" in stdout
    assert app.closed is False


async def test_sources_list_uses_only_registry_factory() -> None:
    registry = SourceRegistry([source("amazon-news", platform="amazon")])
    app_factory_calls: list[str] = []

    async def broken_app_factory() -> FakeApplication:
        app_factory_calls.append("built")
        raise RuntimeError("application dependencies must not be initialized")

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = await run_cli(
        ["sources", "list"],
        app_factory=broken_app_factory,
        registry_factory=lambda: registry,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert "amazon-news" in stdout.getvalue()
    assert stderr.getvalue() == ""
    assert app_factory_calls == []


async def test_sources_coverage_lists_all_ten_platforms_with_compliance_counts() -> None:
    registry = SourceRegistry(
        [
            source("amazon-live", platform="amazon"),
            source(
                "amazon-pending",
                platform="amazon",
                compliance=ComplianceStatus.PENDING_REVIEW,
                enabled=False,
            ),
            source(
                "temu-denied",
                platform="temu",
                compliance=ComplianceStatus.DENIED,
                enabled=False,
            ),
            source(
                "temu-auth",
                platform="temu",
                compliance=ComplianceStatus.AUTHORIZATION_REQUIRED,
                enabled=False,
            ),
        ]
    )
    app = FakeApplication(registry)

    exit_code, stdout, stderr = await invoke(app, "sources", "coverage")

    assert exit_code == 0
    assert stderr == ""
    rows = {line.split()[0]: line.split()[1:] for line in stdout.splitlines()[2:] if line.strip()}
    assert set(rows) == {platform.value for platform in Platform}
    assert rows["amazon"] == [
        "1",
        "1",
        "0",
        "1",
        "0",
        "2",
        "official_public_covered",
    ]
    assert rows["temu"] == [
        "0",
        "0",
        "1",
        "0",
        "1",
        "2",
        "public_covered_seller_center_pending",
    ]
    assert rows["shein"] == ["0", "0", "0", "0", "0", "0", "unconnected"]


@pytest.mark.parametrize(
    "arguments",
    [
        ("run", "--all", "--source", "a-official"),
        ("run",),
        ("unknown",),
    ],
)
async def test_invalid_arguments_exit_two_without_building_application(
    arguments: tuple[str, ...],
) -> None:
    app = FakeApplication(SourceRegistry([]))
    calls: list[str] = []
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = await run_cli(
        list(arguments),
        app_factory=factory_for(app, calls),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "error: invalid arguments\n"
    assert calls == []


async def test_unknown_source_exits_two_without_echoing_query_or_secret() -> None:
    secret = "sk-test-secret-value"
    app = FakeApplication(SourceRegistry([source("known-source", platform="amazon")]))

    exit_code, stdout, stderr = await invoke(
        app,
        "run",
        "--source",
        f"missing-source?token={secret}",
    )

    assert exit_code == 2
    assert stdout == ""
    assert stderr == "error: unknown source_id\n"
    assert secret not in stderr
    assert "?token=" not in stderr
    assert app.run_source_calls == []
    assert app.closed is False


async def test_unknown_source_exits_two_without_building_application() -> None:
    registry = SourceRegistry([source("known-source", platform="amazon")])
    build_calls: list[str] = []

    async def broken_factory() -> FakeApplication:
        build_calls.append("built")
        raise RuntimeError("database initialization failed")

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = await run_cli(
        ["run", "--source", "missing-source"],
        app_factory=broken_factory,
        registry_factory=lambda: registry,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "error: unknown source_id\n"
    assert build_calls == []


async def test_run_source_success_reports_counts_and_exits_zero() -> None:
    registry = SourceRegistry([source("amazon-news", platform="amazon")])
    app = FakeApplication(
        registry,
        source_summaries={"amazon-news": summary("amazon-news", RunStatus.SUCCESS)},
    )

    exit_code, stdout, stderr = await invoke(app, "run", "--source", "amazon-news")

    assert exit_code == 0
    assert stderr == ""
    assert app.run_source_calls == ["amazon-news"]
    assert "amazon-news" in stdout
    assert "success" in stdout
    assert "2" in stdout
    assert "42" in stdout


async def test_probe_source_runs_the_explicit_probe_path() -> None:
    registry = SourceRegistry([source("media-gdelt", platform="temu", enabled=False)])
    app = FakeApplication(
        registry,
        source_summaries={"media-gdelt": summary("media-gdelt", RunStatus.SUCCESS)},
    )

    exit_code, stdout, stderr = await invoke(
        app,
        "probe",
        "--source",
        "media-gdelt",
    )

    assert exit_code == 0
    assert stderr == ""
    assert app.probe_source_calls == ["media-gdelt"]
    assert app.run_source_calls == []
    assert "media-gdelt" in stdout


async def test_run_all_partial_failure_is_reported_and_exits_three() -> None:
    registry = SourceRegistry(
        [
            source("amazon-news", platform="amazon"),
            source("temu-news", platform="temu"),
        ]
    )
    app = FakeApplication(
        registry,
        all_summaries=(
            summary("temu-news", RunStatus.PARTIAL, error_code="fetch_failed"),
            summary("amazon-news", RunStatus.SUCCESS),
        ),
    )

    exit_code, stdout, stderr = await invoke(app, "run", "--all")

    assert exit_code == 3
    assert stderr == ""
    assert app.run_all_calls == 1
    assert stdout.index("amazon-news") < stdout.index("temu-news")
    assert "partial" in stdout
    assert "fetch_failed" in stdout


async def test_busy_source_is_reported_with_retryable_exit_code() -> None:
    registry = SourceRegistry([source("amazon-news", platform="amazon")])
    app = FakeApplication(
        registry,
        source_summaries={
            "amazon-news": summary(
                "amazon-news",
                RunStatus.SKIPPED,
                error_code="source_already_running",
            )
        },
    )

    exit_code, stdout, stderr = await invoke(app, "run", "--source", "amazon-news")

    assert exit_code == 3
    assert "source_already_running" in stdout
    assert stderr == ""


async def test_failed_run_exits_three_but_compliance_skip_is_successful() -> None:
    registry = SourceRegistry(
        [
            source("failed-source", platform="amazon"),
            source(
                "pending-source",
                platform="temu",
                compliance=ComplianceStatus.PENDING_REVIEW,
                enabled=False,
            ),
        ]
    )
    failed_app = FakeApplication(
        registry,
        source_summaries={
            "failed-source": summary("failed-source", RunStatus.FAILED, error_code="fetch_failed")
        },
    )
    skipped_app = FakeApplication(
        registry,
        source_summaries={
            "pending-source": summary(
                "pending-source", RunStatus.SKIPPED, error_code="compliance_not_allowed"
            )
        },
    )

    failed_exit, failed_stdout, _ = await invoke(failed_app, "run", "--source", "failed-source")
    skipped_exit, skipped_stdout, _ = await invoke(skipped_app, "run", "--source", "pending-source")

    assert failed_exit == 3
    assert "failed" in failed_stdout
    assert skipped_exit == 0
    assert "skipped" in skipped_stdout


async def test_health_lists_unknown_and_stored_rows_deterministically() -> None:
    registry = SourceRegistry(
        [
            source("z-source", platform="temu"),
            source("a-source", platform="amazon"),
        ]
    )
    app = FakeApplication(
        registry,
        health_rows=(
            HealthRow(
                source_id="z-source",
                health_status="degraded",
                consecutive_failures=2,
                last_attempt_at=NOW,
                last_success_at=None,
                next_scheduled_at=NOW,
                last_error_code="fetch_failed",
            ),
            HealthRow(source_id="a-source"),
        ),
    )

    exit_code, stdout, stderr = await invoke(app, "health")

    assert exit_code == 0
    assert stderr == ""
    assert stdout.index("a-source") < stdout.index("z-source")
    assert "unknown" in stdout
    assert "degraded" in stdout
    assert "2026-07-20T09:00:00Z" in stdout
    assert "fetch_failed" in stdout


async def test_runtime_failure_exits_three_without_leaking_exception_details() -> None:
    secret = "sk-runtime-secret-value"

    async def broken_factory() -> FakeApplication:
        raise RuntimeError(f"https://example.com/path?api_key={secret}")

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = await run_cli(
        ["health"],
        app_factory=broken_factory,
        stdout=stdout,
        stderr=stderr,
    )

    rendered = stdout.getvalue() + stderr.getvalue()
    assert exit_code == 3
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "error: command failed\n"
    assert secret not in rendered
    assert "?api_key=" not in rendered


async def test_application_is_closed_when_command_raises() -> None:
    class BrokenApplication(FakeApplication):
        async def health(self) -> tuple[HealthRow, ...]:
            raise RuntimeError("database failure")

    app = BrokenApplication(SourceRegistry([]))

    exit_code, _, stderr = await invoke(app, "health")

    assert exit_code == 3
    assert stderr == "error: command failed\n"
    assert app.closed is True


async def test_close_failure_changes_an_otherwise_successful_command_to_exit_three() -> None:
    class CloseFailingApplication(FakeApplication):
        async def aclose(self) -> None:
            raise RuntimeError("close failed with sk-secret-value")

    app = CloseFailingApplication(SourceRegistry([]))

    exit_code, _, stderr = await invoke(app, "health")

    assert exit_code == 3
    assert stderr == "error: command failed\n"
    assert "sk-secret-value" not in stderr


async def test_production_cli_rejects_browser_before_creating_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from commerce_agent import ingestion_cli
    from commerce_agent.config import ProductionConfigurationError

    class BrowserSettings:
        ingestion_browser_enabled = True

    def forbidden_database(url: str) -> object:
        del url
        raise AssertionError("database must not be created")

    monkeypatch.setattr(ingestion_cli, "_IngestionSettings", BrowserSettings)
    monkeypatch.setattr(ingestion_cli, "Database", forbidden_database)

    with pytest.raises(ProductionConfigurationError, match="browser ingestion is unavailable"):
        await build_application()


async def test_cli_browser_config_failure_is_controlled_exit_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from commerce_agent import ingestion_cli

    class BrowserSettings:
        ingestion_browser_enabled = True

    monkeypatch.setattr(ingestion_cli, "_IngestionSettings", BrowserSettings)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = await run_cli(
        ["health"],
        app_factory=build_application,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 3
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "error: command failed\n"


def test_ingestion_cli_settings_accept_cloudflare_doh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGESTION_DNS_MODE", "cloudflare_doh")

    assert _IngestionSettings(_env_file=None).ingestion_dns_mode == "cloudflare_doh"


def test_ingestion_cli_uses_safe_gdelt_defaults() -> None:
    settings = _IngestionSettings(_env_file=None)

    assert settings.gdelt_original_fetch_enabled is False
    assert settings.gdelt_original_fetch_max_per_source == 5
    assert settings.gdelt_media_body_retention_days == 7


async def test_ingestion_cli_wires_overridden_gdelt_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from commerce_agent import ingestion_cli

    captured: dict[str, object] = {}

    class Settings:
        database_url = "sqlite+aiosqlite:///:memory:"
        ingestion_browser_enabled = False
        ingestion_dns_mode = "system"
        ingestion_global_concurrency = 2
        ingestion_domain_rps = 1.0
        ingestion_http_timeout_seconds = 3.0
        ingestion_max_response_bytes = 4096
        ingestion_user_agent = "test-agent"
        snapshot_dir = "."
        gdelt_original_fetch_enabled = True
        gdelt_original_fetch_max_per_source = 7
        gdelt_media_body_retention_days = 7

    class Database:
        session = object()

        def __init__(self, url: str) -> None:
            del url

        async def create_schema(self) -> None:
            pass

        async def dispose(self) -> None:
            pass

    class Repository:
        def __init__(self, session: object) -> None:
            del session

    class HttpClient:
        def __init__(self, **kwargs: object) -> None:
            captured["http_kwargs"] = kwargs

        async def aclose(self) -> None:
            pass

    class Collector:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

    class ApiCollector:
        def __init__(self, client: object, **kwargs: object) -> None:
            del client
            captured["api_kwargs"] = kwargs

    class Service:
        def __init__(self, **kwargs: object) -> None:
            captured["service_kwargs"] = kwargs

        async def initialize(self) -> None:
            captured["initialized"] = True

    monkeypatch.setattr(ingestion_cli, "_IngestionSettings", Settings)
    monkeypatch.setattr(ingestion_cli, "Database", Database)
    monkeypatch.setattr(ingestion_cli, "SqlAlchemyIngestionRepository", Repository)
    monkeypatch.setattr(ingestion_cli, "build_registry", lambda: SourceRegistry([]))
    monkeypatch.setattr(
        ingestion_cli,
        "build_resolver_bundle",
        lambda mode: SimpleNamespace(safety_policy=mode, resources=()),
    )
    monkeypatch.setattr(ingestion_cli, "IngestionHttpClient", HttpClient)
    for name in ("FeedCollector", "SitemapCollector", "HtmlCollector", "BrowserCollector"):
        monkeypatch.setattr(ingestion_cli, name, Collector)
    monkeypatch.setattr(ingestion_cli, "ApiCollector", ApiCollector)
    monkeypatch.setattr(ingestion_cli, "IngestionService", Service)

    application = await build_application()
    await application.aclose()

    assert captured["api_kwargs"] == {
        "fetch_gdelt_originals": True,
        "gdelt_original_fetch_limit": 7,
    }
    assert captured["http_kwargs"]["max_redirects"] == 3  # type: ignore[index]
    assert captured["service_kwargs"]["gdelt_media_body_retention_days"] == 7  # type: ignore[index]
    assert captured["initialized"] is True


async def test_production_application_closes_http_resolver_then_database() -> None:
    events: list[str] = []

    class Service:
        pass

    class Closer:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self) -> None:
            events.append(self.name)

    class Database:
        async def dispose(self) -> None:
            events.append("database")

    app = _ProductionApplication(
        registry=SourceRegistry([]),
        service=Service(),  # type: ignore[arg-type]
        database=Database(),  # type: ignore[arg-type]
        http_client=Closer("http"),  # type: ignore[arg-type]
        resolver_resources=(Closer("resolver"),),
    )

    await app.aclose()

    assert events == ["http", "resolver", "database"]


async def test_cli_partial_construction_closes_http_resolver_then_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from commerce_agent import ingestion_cli

    events: list[str] = []

    class Settings:
        database_url = "sqlite+aiosqlite:///:memory:"
        ingestion_browser_enabled = False
        ingestion_dns_mode = "cloudflare_doh"
        ingestion_global_concurrency = 1
        ingestion_domain_rps = 1.0
        ingestion_http_timeout_seconds = 1.0
        ingestion_max_response_bytes = 1024
        ingestion_user_agent = "test-agent"
        snapshot_dir = "."

    class Database:
        session = object()

        def __init__(self, url: str) -> None:
            del url

        async def create_schema(self) -> None:
            pass

        async def dispose(self) -> None:
            events.append("database")

    class Repository:
        def __init__(self, session: object) -> None:
            del session

        async def sync_sources(self, sources: object) -> None:
            del sources

    class Closer:
        def __init__(self, name: str, **kwargs: object) -> None:
            del kwargs
            self.name = name

        async def aclose(self) -> None:
            events.append(self.name)

    resolver = Closer("resolver")
    monkeypatch.setattr(ingestion_cli, "_IngestionSettings", Settings)
    monkeypatch.setattr(ingestion_cli, "Database", Database)
    monkeypatch.setattr(ingestion_cli, "SqlAlchemyIngestionRepository", Repository)
    monkeypatch.setattr(ingestion_cli, "build_registry", lambda: SourceRegistry([]))
    monkeypatch.setattr(
        ingestion_cli,
        "build_resolver_bundle",
        lambda mode: SimpleNamespace(safety_policy=object(), resources=(resolver,)),
    )
    monkeypatch.setattr(
        ingestion_cli,
        "IngestionHttpClient",
        lambda **kwargs: Closer("http", **kwargs),
    )
    monkeypatch.setattr(
        ingestion_cli,
        "FeedCollector",
        lambda client: (_ for _ in ()).throw(RuntimeError("collector failed")),
    )

    with pytest.raises(RuntimeError, match="collector failed"):
        await build_application()

    assert events == ["http", "resolver", "database"]
