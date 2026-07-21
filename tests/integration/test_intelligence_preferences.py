from datetime import UTC, datetime

from commerce_agent.intelligence.models import RiskProfile
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.intelligence_preferences import (
    SqlAlchemyIntelligencePreferenceStore,
)

NOW = datetime(2026, 7, 21, 1, tzinfo=UTC)


async def test_group_profile_uses_default_then_persists_override_across_sessions(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'preferences.db'}")
    await database.create_schema()
    try:
        store = SqlAlchemyIntelligencePreferenceStore(database.session)

        assert await store.get("chat-one", default=RiskProfile.DEFAULT) is RiskProfile.DEFAULT
        assert (
            await store.get("chat-two", default=RiskProfile.CONSERVATIVE)
            is RiskProfile.CONSERVATIVE
        )

        change = await store.set("chat-one", RiskProfile.AGGRESSIVE, now=NOW)

        assert change.previous is RiskProfile.DEFAULT
        assert change.current is RiskProfile.AGGRESSIVE

        reopened_store = SqlAlchemyIntelligencePreferenceStore(database.session)
        assert (
            await reopened_store.get("chat-one", default=RiskProfile.DEFAULT)
            is RiskProfile.AGGRESSIVE
        )
        assert (
            await reopened_store.get("chat-two", default=RiskProfile.CONSERVATIVE)
            is RiskProfile.CONSERVATIVE
        )
    finally:
        await database.dispose()


async def test_group_profile_update_reports_previous_value(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'preference-update.db'}")
    await database.create_schema()
    try:
        store = SqlAlchemyIntelligencePreferenceStore(database.session)
        await store.set("chat-one", RiskProfile.CONSERVATIVE, now=NOW)

        change = await store.set(
            "chat-one",
            RiskProfile.AGGRESSIVE,
            now=datetime(2026, 7, 21, 2, tzinfo=UTC),
        )

        assert change.previous is RiskProfile.CONSERVATIVE
        assert change.current is RiskProfile.AGGRESSIVE
    finally:
        await database.dispose()
