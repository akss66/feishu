from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from commerce_agent.config import Settings


class _NoopIngestionScheduler:
    service = object()

    def start(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


async def _build_noop_ingestion(
    settings: object,
    database: object,
) -> tuple[_NoopIngestionScheduler, tuple[()]]:
    del settings, database
    return _NoopIngestionScheduler(), ()


def configure_required_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LARK_APP_ID", "cli_test")
    monkeypatch.setenv("LARK_APP_SECRET", "local-test-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-key")
    monkeypatch.setenv("BOT_BIND_CODE", "local-bind-code")


def test_settings_apply_safe_ingestion_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_required_settings(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.ingestion_interval_minutes == 120
    assert settings.ingestion_global_concurrency == 4
    assert settings.ingestion_domain_rps == 1.0
    assert settings.ingestion_http_timeout_seconds == 20.0
    assert settings.ingestion_max_response_bytes == 10_485_760
    assert settings.ingestion_browser_enabled is False
    assert settings.ingestion_dns_mode == "system"
    assert settings.snapshot_dir == Path("data/snapshots")
    assert settings.ingestion_user_agent.strip()
    assert settings.ingestion_scheduler_enabled is False
    assert settings.gdelt_original_fetch_enabled is False
    assert settings.gdelt_original_fetch_max_per_source == 5
    assert settings.gdelt_media_body_retention_days == 7


def test_settings_accept_cloudflare_doh_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_required_settings(monkeypatch)
    monkeypatch.setenv("INGESTION_DNS_MODE", "cloudflare_doh")

    assert Settings(_env_file=None).ingestion_dns_mode == "cloudflare_doh"


def test_settings_reject_unknown_ingestion_dns_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_required_settings(monkeypatch)
    monkeypatch.setenv("INGESTION_DNS_MODE", "unknown")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_intelligence_flags_are_safe_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_required_settings(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.intelligence_analysis_enabled is False
    assert settings.intelligence_daily_report_enabled is False
    assert settings.intelligence_alerts_enabled is False
    assert settings.intelligence_qa_enabled is False
    assert settings.intelligence_timezone == "Asia/Shanghai"
    assert settings.intelligence_daily_hour == 9
    assert settings.intelligence_ai_concurrency == 2
    assert settings.intelligence_evidence_threshold == 75
    assert settings.intelligence_risk_profile == "default"
    assert settings.intelligence_context_ttl_minutes == 30
    assert settings.intelligence_qa_max_turns == 6


def test_official_notice_email_defaults_off_and_password_is_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_required_settings(monkeypatch)
    monkeypatch.setenv("OFFICIAL_NOTICE_EMAIL_PASSWORD", "test-value")

    settings = Settings(_env_file=None)

    assert settings.official_notice_email_enabled is False
    assert "test-value" not in repr(settings)


def test_enabled_official_notice_email_requires_complete_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_required_settings(monkeypatch)
    monkeypatch.setenv("OFFICIAL_NOTICE_EMAIL_ENABLED", "true")

    with pytest.raises(ValidationError, match="official notice email"):
        Settings(_env_file=None)


def test_settings_reject_unknown_intelligence_risk_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_required_settings(monkeypatch)
    monkeypatch.setenv("INTELLIGENCE_RISK_PROFILE", "unknown")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("invalid_threshold", ["0", "74", "76", "100"])
def test_legacy_evidence_threshold_only_accepts_75(
    monkeypatch: pytest.MonkeyPatch,
    invalid_threshold: str,
) -> None:
    configure_required_settings(monkeypatch)
    monkeypatch.setenv("INTELLIGENCE_EVIDENCE_THRESHOLD", invalid_threshold)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("environment_key", "invalid_value"),
    [
        ("INGESTION_INTERVAL_MINUTES", "0"),
        ("INGESTION_GLOBAL_CONCURRENCY", "0"),
        ("INGESTION_DOMAIN_RPS", "0"),
        ("INGESTION_HTTP_TIMEOUT_SECONDS", "0"),
        ("INGESTION_MAX_RESPONSE_BYTES", "0"),
        ("INGESTION_USER_AGENT", "   "),
        ("GDELT_ORIGINAL_FETCH_MAX_PER_SOURCE", "0"),
        ("GDELT_ORIGINAL_FETCH_MAX_PER_SOURCE", "26"),
    ],
)
def test_settings_reject_invalid_ingestion_limits(
    monkeypatch: pytest.MonkeyPatch,
    environment_key: str,
    invalid_value: str,
) -> None:
    configure_required_settings(monkeypatch)
    monkeypatch.setenv(environment_key, invalid_value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_load_required_secrets_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LARK_APP_ID", "cli_test")
    monkeypatch.setenv("LARK_APP_SECRET", "local-test-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-key")
    monkeypatch.setenv("BOT_BIND_CODE", "local-bind-code")

    settings = Settings(_env_file=None)

    assert settings.lark_app_id == "cli_test"
    assert str(settings.deepseek_base_url).rstrip("/") == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-v4-pro"
    assert settings.lark_app_secret.get_secret_value() == "local-test-secret"


def test_settings_reject_blank_required_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LARK_APP_ID", "cli_test")
    monkeypatch.setenv("LARK_APP_SECRET", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-key")
    monkeypatch.setenv("BOT_BIND_CODE", "local-bind-code")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_values_are_redacted_in_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LARK_APP_ID", "cli_test")
    monkeypatch.setenv("LARK_APP_SECRET", "do-not-print-this")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "do-not-print-that")
    monkeypatch.setenv("BOT_BIND_CODE", "do-not-print-code")

    rendered = repr(Settings(_env_file=None))

    assert "do-not-print-this" not in rendered
    assert "do-not-print-that" not in rendered
    assert "do-not-print-code" not in rendered


@pytest.mark.filterwarnings("ignore:pkg_resources is deprecated as an API:UserWarning")
@pytest.mark.filterwarnings(
    "ignore:Deprecated call to `pkg_resources.declare_namespace.*:DeprecationWarning"
)
@pytest.mark.asyncio
async def test_runtime_composes_audited_websocket_and_releases_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from commerce_agent import runtime

    events: list[object] = []
    channel_instances: list[object] = []

    class FakeSettings:
        lark_app_id = "cli_test"
        lark_app_secret = SecretStr("local-lark-secret")
        deepseek_api_key = SecretStr("local-deepseek-key")
        deepseek_base_url = "https://api.deepseek.example/v1/"
        deepseek_model = "deepseek-test"
        deepseek_timeout_seconds = 12.5
        bot_bind_code = SecretStr("local-bind-code")
        database_url = "sqlite+aiosqlite:///:memory:"
        log_level = "INFO"

    class FakeDatabase:
        session = object()

        def __init__(self, url: str) -> None:
            events.append(("database", url))

        async def create_schema(self) -> None:
            events.append("create_schema")

        async def dispose(self) -> None:
            events.append("database_dispose")

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            events.append(("openai", kwargs))

        async def close(self) -> None:
            events.append("openai_close")

    class FakeBindings:
        def __init__(self, session_factory: object) -> None:
            events.append(("bindings", session_factory))

    class FakeLLM:
        def __init__(self, client: object, model: str) -> None:
            events.append(("llm", client, model))

    class FakeService:
        def __init__(
            self,
            bindings: object,
            llm: object,
            bind_code: str,
            **kwargs: object,
        ) -> None:
            assert kwargs["manual_submissions"] is not None
            events.append(("service", bindings, llm, bind_code))

    class FakeChannel:
        def __init__(self, **kwargs: object) -> None:
            channel_instances.append(self)
            events.append(("channel", kwargs))

        async def disconnect(self) -> None:
            events.append("channel_disconnect")

    class FakeAdapter:
        def __init__(self, channel: object, service: object) -> None:
            events.append(("adapter", channel, service))

        async def connect(self) -> None:
            events.append("connect")

        async def close(self) -> None:
            events.append("adapter_close")

    monkeypatch.setattr(runtime, "Settings", FakeSettings)
    monkeypatch.setattr(runtime, "Database", FakeDatabase)
    monkeypatch.setattr(runtime, "AsyncOpenAI", FakeOpenAI)
    monkeypatch.setattr(runtime, "SqlAlchemyGroupBindingStore", FakeBindings)
    monkeypatch.setattr(runtime, "DeepSeekGateway", FakeLLM)
    monkeypatch.setattr(runtime, "BotService", FakeService)
    monkeypatch.setattr(runtime, "FeishuChannel", FakeChannel)
    monkeypatch.setattr(runtime, "FeishuAdapter", FakeAdapter)
    monkeypatch.setattr(runtime, "_build_ingestion", _build_noop_ingestion)

    await runtime.run()

    assert len(channel_instances) == 1
    channel_kwargs = next(event[1] for event in events if event[0] == "channel")
    assert channel_kwargs["app_id"] == "cli_test"
    assert channel_kwargs["app_secret"] == "local-lark-secret"
    assert channel_kwargs["security"].mode == "audit"
    assert channel_kwargs["log_level"].name == "WARNING"
    openai_kwargs = next(event[1] for event in events if event[0] == "openai")
    assert openai_kwargs == {
        "api_key": "local-deepseek-key",
        "base_url": "https://api.deepseek.example/v1",
        "timeout": 12.5,
    }
    assert any(event[0] == "llm" and event[2] == "deepseek-test" for event in events)
    assert events[-5:] == [
        "connect",
        "adapter_close",
        "channel_disconnect",
        "openai_close",
        "database_dispose",
    ]


@pytest.mark.filterwarnings("ignore:pkg_resources is deprecated as an API:UserWarning")
@pytest.mark.filterwarnings(
    "ignore:Deprecated call to `pkg_resources.declare_namespace.*:DeprecationWarning"
)
@pytest.mark.asyncio
async def test_runtime_releases_created_resources_when_adapter_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from commerce_agent import runtime

    events: list[str] = []

    class FakeSettings:
        lark_app_id = "cli_test"
        lark_app_secret = SecretStr("local-lark-secret")
        deepseek_api_key = SecretStr("local-deepseek-key")
        deepseek_base_url = "https://api.deepseek.example/v1/"
        deepseek_model = "deepseek-test"
        deepseek_timeout_seconds = 12.5
        bot_bind_code = SecretStr("local-bind-code")
        database_url = "sqlite+aiosqlite:///:memory:"
        log_level = "INFO"

    class FakeDatabase:
        session = object()

        def __init__(self, url: str) -> None:
            events.append("database")

        async def create_schema(self) -> None:
            events.append("create_schema")

        async def dispose(self) -> None:
            events.append("database_dispose")

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            events.append("openai")

        async def close(self) -> None:
            events.append("openai_close")

    class FakeChannel:
        def __init__(self, **kwargs: object) -> None:
            events.append("channel")

        async def disconnect(self) -> None:
            events.append("channel_disconnect")

    class FailingAdapter:
        def __init__(self, channel: object, service: object) -> None:
            raise RuntimeError("adapter setup failed")

    monkeypatch.setattr(runtime, "Settings", FakeSettings)
    monkeypatch.setattr(runtime, "Database", FakeDatabase)
    monkeypatch.setattr(runtime, "AsyncOpenAI", FakeOpenAI)
    monkeypatch.setattr(runtime, "SqlAlchemyGroupBindingStore", lambda session: object())
    monkeypatch.setattr(runtime, "DeepSeekGateway", lambda client, model: object())
    monkeypatch.setattr(
        runtime,
        "BotService",
        lambda bindings, llm, bind_code, **kwargs: object(),
    )
    monkeypatch.setattr(runtime, "FeishuChannel", FakeChannel)
    monkeypatch.setattr(runtime, "FeishuAdapter", FailingAdapter)
    monkeypatch.setattr(runtime, "_build_ingestion", _build_noop_ingestion)

    with pytest.raises(RuntimeError, match="adapter setup failed"):
        await runtime.run()

    assert events[-3:] == ["channel_disconnect", "openai_close", "database_dispose"]
