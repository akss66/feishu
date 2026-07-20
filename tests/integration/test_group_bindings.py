from commerce_agent.persistence.database import Database
from commerce_agent.persistence.group_bindings import SqlAlchemyGroupBindingStore


async def test_binding_a_new_group_deactivates_the_previous_group(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await database.create_schema()
    store = SqlAlchemyGroupBindingStore(database.session)

    await store.bind("chat-one")
    await store.bind("chat-two")

    assert await store.is_active("chat-one") is False
    assert await store.is_active("chat-two") is True
    assert await store.get_active_chat_id() == "chat-two"
    await database.dispose()
