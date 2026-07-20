from types import SimpleNamespace

from commerce_agent.integrations.feishu import FeishuAdapter


class FakeChannel:
    def __init__(self) -> None:
        self.handlers = {}
        self.sent = []
        self.connected = False

    def on(self, event_name, handler) -> None:
        self.handlers[event_name] = handler

    async def send(self, chat_id, content, options) -> None:
        self.sent.append((chat_id, content, options))

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
    event = SimpleNamespace(chat_id="chat-one", message_id="msg-one", content_text="help")

    await channel.handlers["message"](event)

    assert channel.sent == [
        ("chat-one", {"text": "help content"}, {"reply_to": "msg-one"})
    ]
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
