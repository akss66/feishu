from pathlib import Path

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
    ingestion_interval_minutes: int = Field(default=120, gt=0)
    ingestion_global_concurrency: int = Field(default=4, gt=0)
    ingestion_domain_rps: float = Field(default=1.0, gt=0)
    ingestion_http_timeout_seconds: float = Field(default=20.0, gt=0)
    ingestion_max_response_bytes: int = Field(default=10_485_760, gt=0)
    ingestion_browser_enabled: bool = False
    snapshot_dir: Path = Path("./data/snapshots")
    ingestion_user_agent: str = Field(default="CrossBorderCommerceAgent/0.1", min_length=1)
    ingestion_scheduler_enabled: bool = False

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

    @field_validator("ingestion_user_agent")
    @classmethod
    def reject_blank_ingestion_user_agent(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ingestion user agent must not be blank")
        return value
