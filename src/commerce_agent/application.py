from collections.abc import Callable
from datetime import UTC, datetime
from hmac import compare_digest
from typing import Protocol

from commerce_agent.command_parser import parse_command
from commerce_agent.domain import CommandKind, InboundMessage
from commerce_agent.intelligence.models import RiskProfile, RiskProfileChange


class GroupBindingStore(Protocol):
    async def bind(self, chat_id: str) -> None: ...

    async def get_active_chat_id(self) -> str | None: ...

    async def is_active(self, chat_id: str) -> bool: ...


class LLMGateway(Protocol):
    async def answer_test(self, prompt: str) -> str: ...


class RiskProfileStore(Protocol):
    async def get(self, group_id: str, *, default: RiskProfile) -> RiskProfile: ...

    async def set(
        self,
        group_id: str,
        profile: RiskProfile,
        *,
        now: datetime,
        default: RiskProfile,
    ) -> RiskProfileChange: ...


class QaService(Protocol):
    async def queue_answer(self, message: InboundMessage) -> int: ...


class ManualSubmissionResultPort(Protocol):
    audit_id: str


class ManualSubmissionPort(Protocol):
    async def submit(self, message: InboundMessage) -> ManualSubmissionResultPort: ...


_PROFILE_LABELS = {
    RiskProfile.CONSERVATIVE: "保守",
    RiskProfile.DEFAULT: "默认",
    RiskProfile.AGGRESSIVE: "激进",
}
_PROFILE_ARGUMENTS = {label: profile for profile, label in _PROFILE_LABELS.items()}
_PROFILE_RULES = {
    RiskProfile.CONSERVATIVE: "仅高风险且可信度≥85即时推送。",
    RiskProfile.DEFAULT: "中高风险且可信度≥75即时推送。",
    RiskProfile.AGGRESSIVE: "中高风险且可信度≥60即时推送；60–74为早期信号/待核实。",
}


def profile_status_text(profile: RiskProfile) -> str:
    return f"当前策略：{_PROFILE_LABELS[profile]}。{_PROFILE_RULES[profile]}"


class BotService:
    def __init__(
        self,
        bindings: GroupBindingStore,
        llm: LLMGateway,
        bind_code: str,
        risk_profiles: RiskProfileStore | None = None,
        default_risk_profile: RiskProfile = RiskProfile.DEFAULT,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        qa: QaService | None = None,
        manual_submissions: ManualSubmissionPort | None = None,
    ) -> None:
        self._bindings = bindings
        self._llm = llm
        self._bind_code = bind_code
        self._risk_profiles = risk_profiles
        self._default_risk_profile = default_risk_profile
        self._clock = clock
        self._qa = qa
        self._manual_submissions = manual_submissions

    @property
    def qa_enabled(self) -> bool:
        return self._qa is not None

    async def qa_available(self, chat_id: str) -> bool:
        return self._qa is not None and await self._bindings.is_active(chat_id)

    async def queue_question(self, message: InboundMessage) -> int:
        if self._qa is None:
            raise RuntimeError("qa_disabled")
        if not await self._bindings.is_active(message.chat_id):
            raise RuntimeError("qa_inactive_chat")
        return await self._qa.queue_answer(message)

    async def handle(self, message: InboundMessage) -> str:
        command = parse_command(message.text)
        if command.kind is CommandKind.SUBMIT_INTELLIGENCE:
            if not await self._bindings.is_active(message.chat_id):
                return "❌ 仅当前已绑定群可以提交官方材料。"
            if self._manual_submissions is None:
                return "❌ 官方材料提交功能尚未启用。"
            try:
                result = await self._manual_submissions.submit(message)
            except ValueError:
                return "❌ 材料校验失败，请检查平台、官方账号、原文链接和正文内容。"
            audit_id = result.audit_id
            return (
                "✅ 已接收官方材料，等待正文校验和 AI 分析。"
                f"材料编号：{audit_id}"
            )
        if command.kind is CommandKind.HELP:
            help_text = "可用命令：\n- 帮助\n- 状态\n- 绑定本群 <绑定码>\n- AI测试 <问题>"
            if self._risk_profiles is not None:
                help_text += "\n- 策略 保守 / 策略 默认 / 策略 激进"
            return help_text
        if command.kind is CommandKind.STATUS:
            if await self._bindings.is_active(message.chat_id):
                return "✅ 当前群是已绑定的日报测试群。"
            active = await self._bindings.get_active_chat_id()
            return "☑️ 当前群未绑定。" if active else "☑️ 尚未绑定日报测试群。"
        if command.kind is CommandKind.BIND:
            if not command.argument or not compare_digest(command.argument, self._bind_code):
                return "❌ 绑定码不正确。"
            await self._bindings.bind(message.chat_id)
            return "✅ 已将当前群绑定为日报测试群。"
        if command.kind is CommandKind.AI_TEST:
            if not command.argument:
                return "请输入测试内容，例如：AI测试 用一句话介绍跨境电商。"
            answer = await self._llm.answer_test(command.argument)
            return (
                "🤖 连通性测试（不作为平台政策依据）\n\n"
                "仅为 AI 连通性测试，不代表任何平台政策结论。\n\n"
                f"{answer}"
            )
        if command.kind is CommandKind.RISK_PROFILE:
            if self._risk_profiles is None:
                return "风险策略功能尚未启用。"
            if not await self._bindings.is_active(message.chat_id):
                return "❌ 仅当前已绑定群可以查看或修改风险策略。"
            current = await self._risk_profiles.get(
                message.chat_id,
                default=self._default_risk_profile,
            )
            if not command.argument:
                return profile_status_text(current)
            selected = _PROFILE_ARGUMENTS.get(command.argument)
            if selected is None:
                return "可选策略：保守、默认、激进。"
            change = await self._risk_profiles.set(
                message.chat_id,
                selected,
                now=self._clock(),
                default=self._default_risk_profile,
            )
            return (
                f"✅ 风险策略已更新：{_PROFILE_LABELS[change.previous]}"
                f" → {_PROFILE_LABELS[change.current]}。\n"
                f"{profile_status_text(change.current)}"
            )
        return "暂不支持该指令。发送“帮助”查看可用命令。"
