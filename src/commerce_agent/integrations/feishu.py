from typing import Any

from commerce_agent.application import BotService
from commerce_agent.domain import InboundMessage


class FeishuAdapter:
    def __init__(self, channel: Any, service: BotService) -> None:
        self._channel = channel
        self._service = service
        self._channel.on("message", self._on_message)

    async def _on_message(self, event: Any) -> None:
        inbound = InboundMessage(
            chat_id=event.chat_id,
            message_id=event.message_id,
            text=event.content_text,
        )
        reply = await self._service.handle(inbound)
        await self._channel.send(
            inbound.chat_id,
            {"text": reply},
            {"reply_to": inbound.message_id},
        )

    async def connect(self) -> None:
        await self._channel.connect()
