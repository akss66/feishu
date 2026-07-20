from hmac import compare_digest
from typing import Protocol

from commerce_agent.command_parser import parse_command
from commerce_agent.domain import CommandKind, InboundMessage


class GroupBindingStore(Protocol):
    async def bind(self, chat_id: str) -> None: ...

    async def get_active_chat_id(self) -> str | None: ...

    async def is_active(self, chat_id: str) -> bool: ...


class LLMGateway(Protocol):
    async def answer_test(self, prompt: str) -> str: ...


class BotService:
    def __init__(
        self,
        bindings: GroupBindingStore,
        llm: LLMGateway,
        bind_code: str,
    ) -> None:
        self._bindings = bindings
        self._llm = llm
        self._bind_code = bind_code

    async def handle(self, message: InboundMessage) -> str:
        command = parse_command(message.text)
        if command.kind is CommandKind.HELP:
            return (
                "可用命令：\n"
                "- 帮助\n"
                "- 状态\n"
                "- 绑定本群 <绑定码>\n"
                "- AI测试 <问题>"
            )
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
        return "暂不支持该指令。发送“帮助”查看可用命令。"
