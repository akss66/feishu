from collections.abc import Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from commerce_agent.persistence.models import GroupBinding


class SqlAlchemyGroupBindingStore:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def bind(self, chat_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(update(GroupBinding).values(active=False))
                binding = await session.get(GroupBinding, chat_id)
                if binding is None:
                    session.add(GroupBinding(chat_id=chat_id, active=True))
                else:
                    binding.active = True

    async def get_active_chat_id(self) -> str | None:
        async with self._session_factory() as session:
            result = await session.scalar(
                select(GroupBinding.chat_id).where(GroupBinding.active.is_(True)).limit(1)
            )
            return result

    async def is_active(self, chat_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.scalar(
                select(GroupBinding.active).where(GroupBinding.chat_id == chat_id)
            )
            return result is True
