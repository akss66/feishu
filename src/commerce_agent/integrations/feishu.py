from typing import Any

from commerce_agent.application import BotService
from commerce_agent.domain import InboundMessage


class FeishuAdapter:
    def __init__(self, channel: Any, service: BotService) -> None:
        self._channel = channel
        self._service = service
        self._channel.on("message", self._on_message)

    async def _on_message(self, event: Any) -> None:
        text = event.body_text if hasattr(event, "body_text") else event.content_text
        inbound = InboundMessage(
            chat_id=event.chat_id,
            message_id=event.message_id,
            text=text,
        )
        reply = await self._service.handle(inbound)
        await self._channel.reply(event, {"text": reply})

    async def connect(self) -> None:
        await self._channel.connect()
