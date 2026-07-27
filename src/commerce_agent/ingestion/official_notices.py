from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml

from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    ContentScope,
    Platform,
    SourceDefinition,
    TrustTier,
)

NoticeTransport = Literal["feishu", "email", "api"]

_SENSITIVE_PATTERNS = (
    re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b"),
    re.compile(r"(?<!\d)(?:\+?\d[\d -]{8,}\d)(?!\d)"),
    re.compile(r"(?:订单|order)\s*(?:号|id)?\s*[:：#]?\s*[A-Z0-9-]{6,}", re.I),
    re.compile(r"(?:余额|balance)\s*[:：]?\s*\d", re.I),
    re.compile(r"(?:买家|buyer)\s*(?:id|账号|姓名|邮箱|phone)", re.I),
)


class NoticeValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class OfficialNotice:
    platform: Platform
    source_account: str
    original_url: str
    title: str
    body: str
    published_at: datetime | None
    received_at: datetime
    submitted_by: str
    transport: NoticeTransport


@dataclass(frozen=True, slots=True)
class OfficialAccount:
    account_id: str
    source_id: str
    display_name: str
    platforms: tuple[Platform, ...]
    publisher_key: str
    allowed_hosts: tuple[str, ...]
    transports: tuple[NoticeTransport, ...]

    def as_source_definition(self) -> SourceDefinition:
        entry_url = f"https://{self.allowed_hosts[0]}/"
        return SourceDefinition(
            source_id=self.source_id,
            name=f"{self.display_name}人工官方通知",
            entry_url=entry_url,
            platforms=self.platforms,
            trust_tier=TrustTier.OFFICIAL,
            collector=CollectorKind.MANUAL_NOTICE,
            content_scope=ContentScope.FULL_TEXT,
            attribution=self.display_name,
            publisher_key=self.publisher_key,
            compliance=ComplianceStatus.ALLOWED,
            enabled=True,
            regions=("global",),
            language_hint="zh",
            interval_minutes=1440,
            terms_url=entry_url,
            robots_url=entry_url,
            reviewed_at=date(2026, 7, 27),
            compliance_notes=(
                "Only authenticated team submissions from the exact reviewed account "
                "name are accepted; this source never performs periodic web collection."
            ),
            collector_config={},
        )


class OfficialAccountRegistry:
    def __init__(self, accounts: tuple[OfficialAccount, ...]) -> None:
        self._accounts = accounts
        self._by_name = {account.display_name: account for account in accounts}
        if len(self._by_name) != len(accounts):
            raise ValueError("duplicate_official_account")

    @property
    def accounts(self) -> tuple[OfficialAccount, ...]:
        return self._accounts

    @classmethod
    def from_yaml(
        cls,
        path: Path,
        *,
        document: Mapping[str, object] | None = None,
    ) -> OfficialAccountRegistry:
        raw = document
        if raw is None:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, Mapping):
                raise ValueError("invalid_official_accounts")
            raw = loaded
        entries = raw.get("accounts")
        if not isinstance(entries, list) or not entries:
            raise ValueError("invalid_official_accounts")
        return cls(tuple(_parse_account(entry) for entry in entries))

    def require(self, display_name: str) -> OfficialAccount:
        try:
            return self._by_name[display_name]
        except KeyError:
            raise NoticeValidationError("unknown_official_account") from None

    def require_id(self, account_id: str) -> OfficialAccount:
        for account in self._accounts:
            if account.account_id == account_id:
                return account
        raise NoticeValidationError("unknown_official_account")


def validate_notice(
    notice: OfficialNotice,
    registry: OfficialAccountRegistry,
) -> OfficialAccount:
    account = registry.require(notice.source_account)
    if notice.platform not in account.platforms:
        raise NoticeValidationError("official_account_platform_mismatch")
    if notice.transport not in account.transports:
        raise NoticeValidationError("unsupported_notice_transport")

    parsed = urlsplit(notice.original_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not hostname
        or hostname not in account.allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise NoticeValidationError("untrusted_original_url")
    if not notice.title.strip() or not notice.body.strip():
        raise NoticeValidationError("empty_notice_content")
    if any(pattern.search(notice.body) for pattern in _SENSITIVE_PATTERNS):
        raise NoticeValidationError("account_private_data")
    return account


def _parse_account(value: object) -> OfficialAccount:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_official_account")
    try:
        platforms = tuple(Platform(str(item)) for item in value["platforms"])
        allowed_hosts = tuple(
            str(item).strip().lower().rstrip(".") for item in value["allowed_hosts"]
        )
        transports = tuple(str(item) for item in value["transports"])
        account = OfficialAccount(
            account_id=str(value["account_id"]).strip(),
            source_id=str(value["source_id"]).strip(),
            display_name=str(value["display_name"]).strip(),
            platforms=platforms,
            publisher_key=str(value["publisher_key"]).strip().lower(),
            allowed_hosts=allowed_hosts,
            transports=transports,  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid_official_account") from error
    if (
        not account.account_id
        or not account.source_id
        or not account.display_name
        or not account.publisher_key
        or not account.platforms
        or not account.allowed_hosts
        or not account.transports
        or any(transport not in {"feishu", "email", "api"} for transport in transports)
    ):
        raise ValueError("invalid_official_account")
    return account
