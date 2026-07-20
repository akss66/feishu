import asyncio
import logging
from typing import Any

from commerce_agent.application import BotService
from commerce_agent.command_parser import parse_command
from commerce_agent.domain import CommandKind, InboundMessage

logger = logging.getLogger(__name__)


class FeishuAdapter:
    def __init__(self, channel: Any, service: BotService) -> None:
        self._channel = channel
        self._service = service
        self._pending_tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._channel.on("message", self._on_message)

    async def _on_message(self, event: Any) -> None:
        if self._closed:
            return
        text = event.body_text if hasattr(event, "body_text") else event.content_text
        inbound = InboundMessage(
            chat_id=event.chat_id,
            message_id=event.message_id,
            text=text,
        )
        command = parse_command(text)
        if command.kind is CommandKind.AI_TEST and command.argument:
            await self._channel.reply(event, {"text": "已收到，正在处理中，请稍候。"})
            if self._closed:
                return
            task = asyncio.create_task(
                self._complete_ai_test(event, inbound),
                name="feishu-ai-test",
            )
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
            return

        reply = await self._service.handle(inbound)
        await self._channel.reply(event, {"text": reply})

    async def _complete_ai_test(self, event: Any, inbound: InboundMessage) -> None:
        try:
            reply = await self._service.handle(inbound)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("AI 测试后台处理失败（异常类型：%s）", type(error).__name__)
            await self._reply_background_failure(event)
            return

        try:
            await self._channel.reply(event, {"text": reply})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("AI 测试结果回复失败（异常类型：%s）", type(error).__name__)

    async def _reply_background_failure(self, event: Any) -> None:
        try:
            await self._channel.reply(
                event,
                {"text": "AI 测试处理失败，请稍后重试。"},
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("AI 测试失败提示回复失败（异常类型：%s）", type(error).__name__)

    async def connect(self) -> None:
        await self._channel.connect()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = tuple(self._pending_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            self._pending_tasks.difference_update(tasks)
