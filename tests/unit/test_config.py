import pytest
from pydantic import SecretStr, ValidationError

from commerce_agent.config import Settings


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
        def __init__(self, bindings: object, llm: object, bind_code: str) -> None:
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

    await runtime.run()

    assert len(channel_instances) == 1
    channel_kwargs = next(event[1] for event in events if event[0] == "channel")
    assert channel_kwargs["app_id"] == "cli_test"
    assert channel_kwargs["app_secret"] == "local-lark-secret"
    assert channel_kwargs["security"].mode == "audit"
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
    monkeypatch.setattr(runtime, "BotService", lambda bindings, llm, bind_code: object())
    monkeypatch.setattr(runtime, "FeishuChannel", FakeChannel)
    monkeypatch.setattr(runtime, "FeishuAdapter", FailingAdapter)

    with pytest.raises(RuntimeError, match="adapter setup failed"):
        await runtime.run()

    assert events[-3:] == ["channel_disconnect", "openai_close", "database_dispose"]
