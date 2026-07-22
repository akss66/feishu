import asyncio
import logging
from types import SimpleNamespace

import pytest
from lark_channel import FeishuChannelErrorCode, SendResult
from lark_channel.channel._coerce import coerce_outbound, coerce_send_opts
from lark_channel.channel.errors import SendError
from lark_channel.channel.types import OutboundText, SendOpts

from commerce_agent.integrations.feishu import (
    DeliverySendError,
    FeishuAdapter,
    FeishuDeliveryPort,
)
from commerce_agent.intelligence.delivery import FeishuMessageRenderer
from commerce_agent.intelligence.models import DeliveryClaim, MessageKind


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


async def _wait_for_background_tasks(adapter: FeishuAdapter) -> None:
    tasks = tuple(adapter._pending_tasks)
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
        await asyncio.sleep(0)


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


async def test_unknown_message_is_acknowledged_then_queued_in_same_thread() -> None:
    class QaService:
        qa_enabled = True

        def __init__(self) -> None:
            self.received = None
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def queue_question(self, message) -> int:
            self.received = message
            self.started.set()
            await self.release.wait()
            return 41

        async def qa_available(self, chat_id: str) -> bool:
            return chat_id == "chat-one"

        async def handle(self, message) -> str:
            raise AssertionError("unknown QA must not use the synchronous handler")

    class Delivery:
        def __init__(self) -> None:
            self.sent: list[int] = []

        async def send_id(self, outbox_id: int) -> None:
            self.sent.append(outbox_id)

    channel = FakeChannel()
    service = QaService()
    delivery = Delivery()
    adapter = FeishuAdapter(channel, service, delivery=delivery)
    event = SimpleNamespace(
        chat_id="chat-one",
        message_id="msg-one",
        body_text="亚马逊最近有什么风险？",
        conversation=SimpleNamespace(thread_id="thread-one"),
    )

    await channel.handlers["message"](event)
    await asyncio.wait_for(service.started.wait(), timeout=1)

    assert channel.replies == [(event, {"text": "已收到，正在检索入库资料，请稍候。"})]
    assert service.received.thread_id == "thread-one"
    assert len(adapter._pending_tasks) == 1

    service.release.set()
    await _wait_for_background_tasks(adapter)
    assert delivery.sent == [41]


def test_qa_enabled_adapter_requires_delivery_worker() -> None:
    class QaService:
        qa_enabled = True

    with pytest.raises(ValueError, match="qa_delivery_required"):
        FeishuAdapter(FakeChannel(), QaService())


async def test_qa_prequeue_failure_replies_safely_without_leaking_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    question = "不得写日志的提问"
    secret = "sensitive database detail"

    class FailingQa:
        qa_enabled = True

        async def qa_available(self, chat_id: str) -> bool:
            return True

        async def queue_question(self, message) -> int:
            raise RuntimeError(f"{secret}: {message.text}")

    class Delivery:
        async def send_id(self, outbox_id: int) -> None:
            raise AssertionError(outbox_id)

    channel = FakeChannel()
    adapter = FeishuAdapter(channel, FailingQa(), delivery=Delivery())
    event = SimpleNamespace(chat_id="chat-one", message_id="msg-one", body_text=question)

    with caplog.at_level(logging.ERROR):
        await channel.handlers["message"](event)
        await _wait_for_reply_count(channel, 2)
        await asyncio.sleep(0)

    assert channel.replies[-1] == (event, {"text": "资料检索失败，请稍后重试。"})
    assert "RuntimeError" in caplog.text
    assert question not in caplog.text
    assert secret not in caplog.text
    assert not adapter._pending_tasks


async def test_qa_send_failure_leaves_outbox_for_retry_without_direct_failure_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "sensitive send detail"

    class Qa:
        qa_enabled = True

        async def qa_available(self, chat_id: str) -> bool:
            return True

        async def queue_question(self, message) -> int:
            return 52

    class FailingDelivery:
        async def send_id(self, outbox_id: int) -> None:
            assert outbox_id == 52
            raise RuntimeError(secret)

    channel = FakeChannel()
    adapter = FeishuAdapter(channel, Qa(), delivery=FailingDelivery())
    event = SimpleNamespace(chat_id="chat-one", message_id="msg-one", body_text="风险？")

    with caplog.at_level(logging.ERROR):
        await channel.handlers["message"](event)
        await _wait_for_background_tasks(adapter)

    assert channel.replies == [(event, {"text": "已收到，正在检索入库资料，请稍候。"})]
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text


async def test_close_is_bounded_when_background_qa_swallows_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from commerce_agent.integrations import feishu

    monkeypatch.setattr(feishu, "_TASK_SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    started = asyncio.Event()
    release = asyncio.Event()

    class StubbornQa:
        qa_enabled = True

        async def qa_available(self, chat_id: str) -> bool:
            return True

        async def queue_question(self, message) -> int:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                return 63

    class Delivery:
        def __init__(self) -> None:
            self.sent: list[int] = []

        async def send_id(self, outbox_id: int) -> None:
            self.sent.append(outbox_id)

    delivery = Delivery()
    channel = FakeChannel()
    adapter = FeishuAdapter(channel, StubbornQa(), delivery=delivery)
    event = SimpleNamespace(chat_id="chat-one", message_id="msg-one", body_text="风险？")
    await channel.handlers["message"](event)
    await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.wait_for(adapter.close(), timeout=0.2)

    assert adapter._pending_tasks
    release.set()
    await _wait_for_background_tasks(adapter)
    assert delivery.sent == []


async def test_unbound_unknown_message_never_starts_qa_or_acknowledges_search() -> None:
    class BoundOnlyQa:
        qa_enabled = True

        async def qa_available(self, chat_id: str) -> bool:
            return False

        async def queue_question(self, message) -> int:
            raise AssertionError("unbound group must not queue QA")

        async def handle(self, message) -> str:
            return "暂不支持该指令。发送“帮助”查看可用命令。"

    class Delivery:
        async def send_id(self, outbox_id: int) -> None:
            raise AssertionError(outbox_id)

    channel = FakeChannel()
    adapter = FeishuAdapter(channel, BoundOnlyQa(), delivery=Delivery())
    event = SimpleNamespace(chat_id="chat-other", message_id="msg-one", body_text="风险？")

    await channel.handlers["message"](event)

    assert channel.replies == [(event, {"text": "暂不支持该指令。发送“帮助”查看可用命令。"})]
    assert not adapter._pending_tasks


async def test_delivery_port_is_available_from_feishu_integration() -> None:
    class SuccessfulDeliveryChannel(FakeChannel):
        async def send(self, chat_id, content, options) -> object:
            self.sent.append((chat_id, content, options))
            return SimpleNamespace(success=True, message_id="om_delivery", error=None)

    channel = SuccessfulDeliveryChannel()
    claim = DeliveryClaim(
        id=1,
        idempotency_key="delivery-one",
        group_id="chat-one",
        kind=MessageKind.QA_ANSWER,
        payload={"text": "有据回答"},
        reply_to_message_id="om_parent",
        reply_in_thread=True,
        attempt_count=1,
        lease_token="lease-one",
    )

    message_id = await FeishuDeliveryPort(channel, FeishuMessageRenderer()).send(claim)

    assert message_id == "om_delivery"


async def test_delivery_port_accepts_real_sdk_send_result_and_send_options() -> None:
    class SdkContractChannel(FakeChannel):
        async def send(self, chat_id, content, options) -> SendResult:
            self.sent.append((chat_id, content, options))
            return SendResult.ok(message_id="om_sdk")

    channel = SdkContractChannel()
    claim = DeliveryClaim(
        id=1,
        idempotency_key="delivery-sdk-one",
        group_id="chat-one",
        kind=MessageKind.QA_ANSWER,
        payload={"text": "有据回答"},
        reply_to_message_id="om_parent",
        reply_in_thread=True,
        attempt_count=1,
        lease_token="lease-one",
    )

    message_id = await FeishuDeliveryPort(channel, FeishuMessageRenderer()).send(claim)
    sdk_message = coerce_outbound(channel.sent[0][1])
    sdk_options = coerce_send_opts(channel.sent[0][2])

    assert message_id == "om_sdk"
    assert isinstance(sdk_message, OutboundText)
    assert sdk_message.text == "有据回答"
    assert isinstance(sdk_options, SendOpts)
    assert sdk_options.reply_to == "om_parent"
    assert sdk_options.reply_in_thread is True
    assert sdk_options.uuid == "delivery-sdk-one"


async def test_delivery_port_reduces_real_sdk_send_error_without_logging_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailedSdkChannel(FakeChannel):
        async def send(self, chat_id, content, options) -> SendResult:
            self.sent.append((chat_id, content, options))
            error = SendError(
                code=FeishuChannelErrorCode.RATE_LIMITED,
                retryable=True,
                hint="secret sdk hint",
                raw_code=999_999,
            )
            return SendResult.fail(error, raw={"token": "secret sdk token"})

    with caplog.at_level(logging.DEBUG), pytest.raises(DeliverySendError) as raised:
        await FeishuDeliveryPort(FailedSdkChannel(), FeishuMessageRenderer()).send(
            DeliveryClaim(
                id=1,
                idempotency_key="delivery-sdk-failure",
                group_id="chat-sensitive",
                kind=MessageKind.QA_ANSWER,
                payload={"text": "sensitive answer"},
                reply_to_message_id=None,
                reply_in_thread=False,
                attempt_count=1,
                lease_token="lease-one",
            )
        )

    assert raised.value.code == "rate_limited"
    for forbidden in (
        "secret sdk hint",
        "secret sdk token",
        "chat-sensitive",
        "sensitive answer",
        "999999",
    ):
        assert forbidden not in caplog.text
