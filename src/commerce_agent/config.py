from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    lark_app_id: str = Field(min_length=1)
    lark_app_secret: SecretStr
    deepseek_api_key: SecretStr
    deepseek_base_url: HttpUrl = HttpUrl("https://api.deepseek.com")
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    bot_bind_code: SecretStr
    database_url: str = "sqlite+aiosqlite:///./commerce_agent.db"
    log_level: str = "INFO"

    @field_validator("lark_app_secret", "deepseek_api_key", "bot_bind_code")
    @classmethod
    def reject_blank_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("secret value must not be blank")
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return normalized
