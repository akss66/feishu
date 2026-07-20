import pytest
from pydantic import ValidationError

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
