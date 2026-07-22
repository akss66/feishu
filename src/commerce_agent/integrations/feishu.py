from __future__ import annotations

import asyncio
import logging
from typing import Any

from commerce_agent.application import BotService
from commerce_agent.command_parser import parse_command
from commerce_agent.domain import CommandKind, InboundMessage
from commerce_agent.intelligence.delivery import (
    DeliverySendError,
    FeishuDeliveryPort,
    safe_feishu_error_code,
)

logger = logging.getLogger(__name__)
_TASK_SHUTDOWN_TIMEOUT_SECONDS = 1.0


class FeishuAdapter:
    def __init__(
        self,
        channel: Any,
        service: BotService,
        delivery: Any | None = None,
        qa_concurrency: int = 2,
    ) -> None:
        self._channel = channel
        self._service = service
        self._delivery = delivery
        self._pending_tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._qa_slots_in_use = 0
        self._qa_concurrency = qa_concurrency
        if getattr(service, "qa_enabled", False):
            if delivery is None:
                raise ValueError("qa_delivery_required")
            if qa_concurrency <= 0:
                raise ValueError("qa_concurrency_must_be_positive")
        self._channel.on("message", self._on_message)

    async def _on_message(self, event: Any) -> None:
        if self._closed:
            return
        text = event.body_text if hasattr(event, "body_text") else event.content_text
        inbound = InboundMessage(
            chat_id=event.chat_id,
            message_id=event.message_id,
            text=text,
            thread_id=getattr(getattr(event, "conversation", None), "thread_id", None),
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

        if (
            command.kind is CommandKind.UNKNOWN
            and getattr(self._service, "qa_enabled", False)
            and await self._service.qa_available(event.chat_id)
        ):
            if not self._reserve_qa_slot():
                logger.warning("grounded qa capacity exhausted")
                await self._channel.reply(
                    event,
                    {"text": "当前问答请求较多，请稍后重试。"},
                )
                return
            handed_off = False
            try:
                await self._channel.reply(
                    event,
                    {"text": "已收到，正在检索入库资料，请稍候。"},
                )
                if self._closed:
                    return
                background = self._run_reserved_qa(event, inbound)
                try:
                    task = asyncio.create_task(
                        background,
                        name="feishu-grounded-qa",
                    )
                except BaseException:
                    background.close()
                    raise
                handed_off = True
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)
            finally:
                if not handed_off:
                    self._release_qa_slot()
            return

        reply = await self._service.handle(inbound)
        await self._channel.reply(event, {"text": reply})

    def _reserve_qa_slot(self) -> bool:
        if self._qa_slots_in_use >= self._qa_concurrency:
            return False
        self._qa_slots_in_use += 1
        return True

    def _release_qa_slot(self) -> None:
        self._qa_slots_in_use -= 1

    async def _run_reserved_qa(self, event: Any, inbound: InboundMessage) -> None:
        try:
            await self._queue_and_send_qa(event, inbound)
        finally:
            self._release_qa_slot()

    async def _queue_and_send_qa(self, event: Any, inbound: InboundMessage) -> None:
        try:
            outbox_id = await self._service.queue_question(inbound)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "grounded qa failed before queue (exception_type=%s)",
                type(error).__name__,
            )
            await self._reply_qa_prequeue_failure(event)
            return
        if self._closed:
            return
        try:
            await self._delivery.send_id(outbox_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "grounded qa delivery failed (exception_type=%s)",
                type(error).__name__,
            )

    async def _reply_qa_prequeue_failure(self, event: Any) -> None:
        if self._closed:
            return
        try:
            await self._channel.reply(event, {"text": "资料检索失败，请稍后重试。"})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "grounded qa failure reply failed (exception_type=%s)",
                type(error).__name__,
            )

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
            done, _ = await asyncio.wait(
                tasks,
                timeout=_TASK_SHUTDOWN_TIMEOUT_SECONDS,
            )
            self._pending_tasks.difference_update(done)


__all__ = [
    "DeliverySendError",
    "FeishuAdapter",
    "FeishuDeliveryPort",
    "safe_feishu_error_code",
]
