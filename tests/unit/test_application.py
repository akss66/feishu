from datetime import UTC, datetime

import pytest

from commerce_agent.application import BotService
from commerce_agent.domain import InboundMessage
from commerce_agent.intelligence.models import RiskProfile, RiskProfileChange


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


class FakePreferences:
    def __init__(self) -> None:
        self.values: dict[str, RiskProfile] = {}

    async def get(self, group_id: str, *, default: RiskProfile) -> RiskProfile:
        return self.values.get(group_id, default)

    async def set(
        self,
        group_id: str,
        profile: RiskProfile,
        *,
        now: datetime,
        default: RiskProfile,
    ) -> RiskProfileChange:
        assert now == datetime(2026, 7, 22, tzinfo=UTC)
        previous = self.values.get(group_id, default)
        self.values[group_id] = profile
        return RiskProfileChange(previous=previous, current=profile)


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


async def test_existing_help_is_byte_for_byte_compatible_without_intelligence() -> None:
    service = BotService(FakeBindingStore(), FakeLLM(), bind_code="correct-code")

    assert await service.handle(message("帮助")) == (
        "可用命令：\n- 帮助\n- 状态\n- 绑定本群 <绑定码>\n- AI测试 <问题>"
    )


async def test_bound_group_queries_exact_profile_threshold() -> None:
    bindings = FakeBindingStore()
    bindings.active_chat_id = "chat-one"
    service = BotService(
        bindings,
        FakeLLM(),
        bind_code="correct-code",
        risk_profiles=FakePreferences(),
        default_risk_profile=RiskProfile.CONSERVATIVE,
    )

    reply = await service.handle(message("策略"))

    assert reply == "当前策略：保守。仅高风险且可信度≥85即时推送。"


async def test_bound_group_changes_profile_and_reports_old_to_new() -> None:
    bindings = FakeBindingStore()
    bindings.active_chat_id = "chat-one"
    preferences = FakePreferences()
    service = BotService(
        bindings,
        FakeLLM(),
        bind_code="correct-code",
        risk_profiles=preferences,
        clock=lambda: datetime(2026, 7, 22, tzinfo=UTC),
    )

    reply = await service.handle(message("策略 激进"))

    assert "默认 → 激进" in reply
    assert "可信度≥60" in reply
    assert "60–74为早期信号/待核实" in reply
    assert "管理员" not in reply
    assert preferences.values["chat-one"] is RiskProfile.AGGRESSIVE


async def test_unbound_group_cannot_query_or_change_profile() -> None:
    bindings = FakeBindingStore()
    bindings.active_chat_id = "chat-one"
    preferences = FakePreferences()
    service = BotService(
        bindings,
        FakeLLM(),
        bind_code="correct-code",
        risk_profiles=preferences,
    )

    query = await service.handle(message("策略", chat_id="chat-other"))
    change = await service.handle(message("策略 激进", chat_id="chat-other"))

    assert query == "❌ 仅当前已绑定群可以查看或修改风险策略。"
    assert change == query
    assert preferences.values == {}


async def test_unknown_profile_name_lists_only_supported_values() -> None:
    bindings = FakeBindingStore()
    bindings.active_chat_id = "chat-one"
    service = BotService(
        bindings,
        FakeLLM(),
        bind_code="correct-code",
        risk_profiles=FakePreferences(),
    )

    assert await service.handle(message("策略 超激进")) == "可选策略：保守、默认、激进。"


async def test_qa_exposes_queue_without_changing_unknown_synchronous_reply() -> None:
    class Qa:
        async def queue_answer(self, inbound: InboundMessage) -> int:
            assert inbound.text == "亚马逊最近有什么风险？"
            return 17

    bindings = FakeBindingStore()
    bindings.active_chat_id = "chat-one"
    service = BotService(
        bindings,
        FakeLLM(),
        bind_code="correct-code",
        qa=Qa(),
    )

    assert service.qa_enabled is True
    assert await service.queue_question(message("亚马逊最近有什么风险？")) == 17
    assert await service.handle(message("亚马逊最近有什么风险？")) == (
        "暂不支持该指令。发送“帮助”查看可用命令。"
    )


async def test_bound_group_can_submit_official_intelligence() -> None:
    class Submissions:
        async def submit(self, inbound: InboundMessage):
            assert inbound.sender_id == "user-123"
            return type("Result", (), {"audit_id": "audit123"})()

    bindings = FakeBindingStore()
    bindings.active_chat_id = "chat-one"
    service = BotService(
        bindings,
        FakeLLM(),
        bind_code="correct-code",
        manual_submissions=Submissions(),
    )
    inbound = InboundMessage(
        chat_id="chat-one",
        message_id="msg-one",
        text="提交情报\n平台: amazon",
        sender_id="user-123",
    )

    reply = await service.handle(inbound)

    assert reply == (
        "✅ 已接收官方材料，等待正文校验和 AI 分析。"
        "材料编号：audit123"
    )


async def test_unbound_group_cannot_submit_official_intelligence() -> None:
    class Submissions:
        async def submit(self, inbound: InboundMessage):
            raise AssertionError(inbound)

    service = BotService(
        FakeBindingStore(),
        FakeLLM(),
        bind_code="correct-code",
        manual_submissions=Submissions(),
    )

    reply = await service.handle(message("提交情报\n平台: amazon"))

    assert reply == "❌ 仅当前已绑定群可以提交官方材料。"


async def test_qa_rejects_direct_queue_from_unbound_group_before_model_work() -> None:
    class Qa:
        async def queue_answer(self, inbound: InboundMessage) -> int:
            raise AssertionError(f"must not queue unbound question: {inbound.text}")

    bindings = FakeBindingStore()
    bindings.active_chat_id = "chat-one"
    service = BotService(
        bindings,
        FakeLLM(),
        bind_code="correct-code",
        qa=Qa(),
    )

    assert await service.qa_available("chat-one") is True
    assert await service.qa_available("chat-other") is False
    with pytest.raises(RuntimeError, match="qa_inactive_chat"):
        await service.queue_question(message("风险？", chat_id="chat-other"))
