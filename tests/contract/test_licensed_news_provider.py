from datetime import UTC, datetime

from commerce_agent.ingestion.models import Platform
from commerce_agent.ingestion.providers import (
    DisabledLicensedNewsProvider,
    LicensedNewsProvider,
)

START = datetime(2026, 7, 20, tzinfo=UTC)
END = datetime(2026, 7, 21, tzinfo=UTC)


async def assert_provider_contract(provider: LicensedNewsProvider) -> None:
    items = await provider.fetch(
        platforms=(Platform.AMAZON,),
        window_start=START,
        window_end=END,
    )
    for item in items:
        assert item.platform is Platform.AMAZON
        assert item.original_url.startswith("https://")
        assert item.publisher_key
        assert item.attribution
        assert item.body.strip()
        assert START <= item.received_at < END


async def test_disabled_provider_is_inert() -> None:
    provider = DisabledLicensedNewsProvider()

    assert await provider.fetch(
        platforms=tuple(Platform),
        window_start=START,
        window_end=END,
    ) == ()
    await assert_provider_contract(provider)
