import asyncio
import logging
from types import SimpleNamespace

import pytest

from commerce_agent.integrations.feishu import FeishuAdapter


class FakeChannel:
    def __init__(self) -> None:
        self.handlers = {}
        self.replies = []
        self.sent = []
        self.connected = False
        self.reply_added = asyncio.Event()

    def on(self, event_name, handler) -> None:
        self.handlers[event_name] = handler

    async def send(self, chat_id, content, options) -> None:
        self.sent.append((chat_id, content, options))

    async def reply(self, event, content) -> None:
        self.replies.append((event, content))
        self.reply_added.set()

    async def connect(self) -> None:
        self.connected = True


class FakeService:
    async def handle(self, message) -> str:
        assert message.chat_id == "chat-one"
        assert message.message_id == "msg-one"
        assert message.text == "help"
        return "help content"


async def test_message_event_is_replied_to_in_thread() -> None:
    channel = FakeChannel()
    adapter = FeishuAdapter(channel, FakeService())
    event = SimpleNamespace(
        chat_id="chat-one",
        message_id="msg-one",
        content_text="help",
        conversation=SimpleNamespace(thread_id="thread-one"),
    )

    await channel.handlers["message"](event)

    assert channel.replies == [(event, {"text": "help content"})]
    await adapter.connect()
    assert channel.connected is True


async def test_message_event_prefers_sdk_body_text_over_mentioned_content() -> None:
    class RecordingService:
        def __init__(self) -> None:
            self.message = None

        async def handle(self, message) -> str:
            self.message = message
            return "help content"

    channel = FakeChannel()
    service = RecordingService()
    FeishuAdapter(channel, service)
    event = SimpleNamespace(
        chat_id="chat-one",
        message_id="msg-one",
        body_text="\u5e2e\u52a9",
        content_text="@\u673a\u5668\u4eba \u5e2e\u52a9",
    )

    await channel.handlers["message"](event)

    assert service.message.text == "\u5e2e\u52a9"


async def _wait_for_reply_count(channel: FakeChannel, count: int) -> None:
    async with asyncio.timeout(1):
        while len(channel.replies) < count:
            channel.reply_added.clear()
            await channel.reply_added.wait()


async def test_ai_test_acknowledges_before_background_service_finishes() -> None:
    class BlockingService:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def handle(self, message) -> str:
            assert message.text == "AI测试 请总结这个问题"
            self.started.set()
            await self.release.wait()
            return "最终答案"

    channel = FakeChannel()
    service = BlockingService()
    adapter = FeishuAdapter(channel, service)
    event = SimpleNamespace(
        chat_id="chat-one",
        message_id="msg-one",
        body_text="AI测试 请总结这个问题",
    )

    handler_task = asyncio.create_task(channel.handlers["message"](event))
    await asyncio.wait_for(service.started.wait(), timeout=1)
    try:
        assert channel.replies == [(event, {"text": "已收到，正在处理中，请稍候。"})]
        assert handler_task.done()
        assert len(adapter._pending_tasks) == 1
    finally:
        service.release.set()
        await handler_task

    await _wait_for_reply_count(channel, 2)
    await asyncio.sleep(0)

    assert channel.replies[-1] == (event, {"text": "最终答案"})
    assert not adapter._pending_tasks


async def test_ai_test_background_failure_replies_safely(
    caplog: pytest.LogCaptureFixture,
) -> None:
    prompt = "AI测试 不得写入日志的提问"
    secret_values = [
        "不得写入日志的提问",
        "sk-local-secret",
        "local-bind-code",
        "sensitive exception detail",
    ]

    class FailingService:
        async def handle(self, message) -> str:
            raise RuntimeError(" ".join(secret_values))

    channel = FakeChannel()
    adapter = FeishuAdapter(channel, FailingService())
    event = SimpleNamespace(chat_id="chat-one", message_id="msg-one", body_text=prompt)

    with caplog.at_level(logging.ERROR):
        await channel.handlers["message"](event)
        await _wait_for_reply_count(channel, 2)
        await asyncio.sleep(0)

    assert channel.replies[0] == (event, {"text": "已收到，正在处理中，请稍候。"})
    assert "失败" in channel.replies[1][1]["text"]
    assert channel.replies[1][0] is event
    assert "RuntimeError" in caplog.text
    assert prompt not in caplog.text
    for secret in secret_values:
        assert secret not in caplog.text
    assert not adapter._pending_tasks


async def test_ai_test_without_argument_stays_synchronous() -> None:
    class RecordingService:
        def __init__(self) -> None:
            self.calls = []

        async def handle(self, message) -> str:
            self.calls.append(message)
            return "请输入测试内容，例如：AI测试 用一句话介绍跨境电商。"

    channel = FakeChannel()
    service = RecordingService()
    adapter = FeishuAdapter(channel, service)
    event = SimpleNamespace(chat_id="chat-one", message_id="msg-one", body_text="AI测试")

    await channel.handlers["message"](event)

    assert len(service.calls) == 1
    assert channel.replies == [
        (event, {"text": "请输入测试内容，例如：AI测试 用一句话介绍跨境电商。"})
    ]
    assert not adapter._pending_tasks


async def test_close_cancels_and_awaits_pending_ai_test_tasks() -> None:
    class CancellableService:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def handle(self, message) -> str:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    channel = FakeChannel()
    service = CancellableService()
    adapter = FeishuAdapter(channel, service)
    event = SimpleNamespace(
        chat_id="chat-one",
        message_id="msg-one",
        body_text="AI测试 一直等待",
    )

    await channel.handlers["message"](event)
    await asyncio.wait_for(service.started.wait(), timeout=1)

    await adapter.close()

    assert service.cancelled.is_set()
    assert not adapter._pending_tasks
