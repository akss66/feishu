from __future__ import annotations

from typing import Protocol

from commerce_agent.ingestion.official_notices import OfficialNotice


class OfficialNoticeProvider(Protocol):
    async def poll(self) -> tuple[OfficialNotice, ...]: ...
