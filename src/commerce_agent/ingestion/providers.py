from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from commerce_agent.ingestion.models import Platform
from commerce_agent.ingestion.official_notices import OfficialNotice


class OfficialNoticeProvider(Protocol):
    async def poll(self) -> tuple[OfficialNotice, ...]: ...


@dataclass(frozen=True, slots=True)
class LicensedArticle:
    platform: Platform
    publisher_key: str
    attribution: str
    original_url: str
    title: str
    body: str
    published_at: datetime | None
    received_at: datetime


class LicensedNewsProvider(Protocol):
    async def fetch(
        self,
        *,
        platforms: tuple[Platform, ...],
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[LicensedArticle, ...]: ...


class DisabledLicensedNewsProvider:
    async def fetch(
        self,
        *,
        platforms: tuple[Platform, ...],
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[LicensedArticle, ...]:
        del platforms, window_start, window_end
        return ()
