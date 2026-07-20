from commerce_agent.application import BotService
from commerce_agent.domain import InboundMessage


class FakeBindingStore:
    def __init__(self) -> None:
        self.active_chat_id: str | None = None

    async def bind(self, chat_id: str) -> None:
        self.active_chat_id = chat_id

    async def get_active_chat_id(self) -> str | None:
        return self.active_chat_id

    async def is_active(self, chat_id: str) -> bool:
        return self.active_chat_id == chat_id


class FakeLLM:
    async def answer_test(self, prompt: str) -> str:
        return f"模型回复：{prompt}"


def message(text: str, chat_id: str = "chat-one") -> InboundMessage:
    return InboundMessage(chat_id=chat_id, message_id="msg-one", text=text)


async def test_bind_rejects_wrong_code() -> None:
    service = BotService(FakeBindingStore(), FakeLLM(), bind_code="correct-code")

    reply = await service.handle(message("绑定本群 wrong-code"))

    assert reply == "❌ 绑定码不正确。"


async def test_bind_activates_the_current_chat() -> None:
    store = FakeBindingStore()
    service = BotService(store, FakeLLM(), bind_code="correct-code")

    reply = await service.handle(message("绑定本群 correct-code"))

    assert reply == "✅ 已将当前群绑定为日报测试群。"
    assert store.active_chat_id == "chat-one"


async def test_ai_test_is_labeled_as_non_policy_output() -> None:
    service = BotService(FakeBindingStore(), FakeLLM(), bind_code="correct-code")

    reply = await service.handle(message("AI测试 你好"))

    assert reply == (
        "🤖 连通性测试（不作为平台政策依据）\n\n"
        "仅为 AI 连通性测试，不代表任何平台政策结论。\n\n"
        "模型回复：你好"
    )
