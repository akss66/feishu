from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProductionConfigurationError(ValueError):
    """A controlled rejection of an unsupported production configuration."""


def require_browser_ingestion_disabled(enabled: bool) -> None:
    if enabled:
        raise ProductionConfigurationError(
            "browser ingestion is unavailable in production until shared resource budgets exist"
        )


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
    ingestion_dns_mode: Literal["system", "cloudflare_doh"] = "system"
    snapshot_dir: Path = Path("./data/snapshots")
    ingestion_user_agent: str = Field(default="CrossBorderCommerceAgent/0.1", min_length=1)
    ingestion_scheduler_enabled: bool = False
    gdelt_original_fetch_enabled: bool = False
    gdelt_original_fetch_max_per_source: int = Field(default=5, ge=1, le=25)
    gdelt_media_body_retention_days: Literal[7] = 7
    intelligence_analysis_enabled: bool = False
    intelligence_daily_report_enabled: bool = False
    intelligence_alerts_enabled: bool = False
    intelligence_qa_enabled: bool = False
    intelligence_timezone: str = "Asia/Shanghai"
    intelligence_daily_hour: int = Field(default=9, ge=0, le=23)
    intelligence_ai_concurrency: int = Field(default=2, ge=1, le=8)
    intelligence_evidence_threshold: Literal[75] = 75
    intelligence_risk_profile: Literal["conservative", "default", "aggressive"] = "default"
    intelligence_context_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    intelligence_qa_max_turns: int = Field(default=6, ge=1, le=20)
    official_notice_email_enabled: bool = False
    official_notice_email_host: str | None = None
    official_notice_email_port: int = Field(default=993, ge=1, le=65535)
    official_notice_email_username: str | None = None
    official_notice_email_password: SecretStr | None = None
    official_notice_email_folder: str = "INBOX"
    official_notice_email_allowed_senders: str = ""
    official_notice_email_max_message_bytes: int = Field(default=1_000_000, ge=1)
    official_notice_email_max_attachment_bytes: int = Field(default=2_000_000, ge=1)

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

    @model_validator(mode="after")
    def validate_official_notice_email(self) -> Settings:
        if not self.official_notice_email_enabled:
            return self
        password = self.official_notice_email_password
        if (
            not (self.official_notice_email_host or "").strip()
            or not (self.official_notice_email_username or "").strip()
            or password is None
            or not password.get_secret_value().strip()
            or not self.official_notice_email_allowed_senders.strip()
        ):
            raise ValueError(
                "official notice email requires host, username, password, and allowed senders"
            )
        return self
