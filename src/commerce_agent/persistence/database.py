from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from commerce_agent.persistence.models import Base


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url)
        if self.engine.url.get_backend_name() == "sqlite":
            event.listen(self.engine.sync_engine, "connect", _configure_sqlite)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()


def _configure_sqlite(dbapi_connection, connection_record) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
