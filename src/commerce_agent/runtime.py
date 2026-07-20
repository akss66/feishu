import logging

from lark_channel import FeishuChannel, SecurityConfig
from openai import AsyncOpenAI

from commerce_agent.application import BotService
from commerce_agent.config import Settings
from commerce_agent.integrations.deepseek import DeepSeekGateway
from commerce_agent.integrations.feishu import FeishuAdapter
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.group_bindings import SqlAlchemyGroupBindingStore


async def run() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    database: Database | None = None
    openai_client: AsyncOpenAI | None = None
    channel: FeishuChannel | None = None
    adapter: FeishuAdapter | None = None
    try:
        database = Database(settings.database_url)
        await database.create_schema()
        bindings = SqlAlchemyGroupBindingStore(database.session)

        openai_client = AsyncOpenAI(
            api_key=settings.deepseek_api_key.get_secret_value(),
            base_url=str(settings.deepseek_base_url).rstrip("/"),
            timeout=settings.deepseek_timeout_seconds,
        )
        llm = DeepSeekGateway(openai_client, settings.deepseek_model)
        service = BotService(bindings, llm, settings.bot_bind_code.get_secret_value())
        channel = FeishuChannel(
            app_id=settings.lark_app_id,
            app_secret=settings.lark_app_secret.get_secret_value(),
            security=SecurityConfig(mode="audit"),
        )
        adapter = FeishuAdapter(channel, service)

        await adapter.connect()
    finally:
        try:
            if adapter is not None:
                await adapter.close()
        finally:
            try:
                if channel is not None:
                    await channel.disconnect()
            finally:
                try:
                    if openai_client is not None:
                        await openai_client.close()
                finally:
                    if database is not None:
                        await database.dispose()
