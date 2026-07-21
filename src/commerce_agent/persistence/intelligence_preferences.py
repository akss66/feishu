from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from commerce_agent.intelligence.models import RiskProfile, RiskProfileChange
from commerce_agent.persistence.models import GroupIntelligencePreference


class SqlAlchemyIntelligencePreferenceStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, group_id: str, *, default: RiskProfile) -> RiskProfile:
        async with self._session_factory() as session:
            row = await session.get(GroupIntelligencePreference, group_id)
            return default if row is None else RiskProfile(row.risk_profile)

    async def set(
        self,
        group_id: str,
        profile: RiskProfile,
        *,
        now: datetime,
        default: RiskProfile = RiskProfile.DEFAULT,
    ) -> RiskProfileChange:
        async with self._session_factory() as session, session.begin():
            row = await session.get(GroupIntelligencePreference, group_id)
            previous = default if row is None else RiskProfile(row.risk_profile)
            if row is None:
                session.add(
                    GroupIntelligencePreference(
                        group_id=group_id,
                        risk_profile=profile.value,
                        updated_at=now,
                    )
                )
            else:
                row.risk_profile = profile.value
                row.updated_at = now
        return RiskProfileChange(previous=previous, current=profile)
