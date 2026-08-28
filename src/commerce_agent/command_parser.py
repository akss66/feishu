from commerce_agent.domain import Command, CommandKind

_PROFILE_CHOICES = frozenset({"保守", "默认", "激进"})
_PROFILE_BRACKETS = {"[": "]", "【": "】", "(": ")", "（": "）"}


def _profile_argument(value: str) -> str:
    argument = value.strip()
    if len(argument) >= 2 and _PROFILE_BRACKETS.get(argument[0]) == argument[-1]:
        argument = argument[1:-1].strip()
    return argument


def parse_command(text: str) -> Command:
    submission = text.replace("\r\n", "\n").strip()
    if submission.split("\n", 1)[0].strip() == "提交情报":
        return Command(CommandKind.SUBMIT_INTELLIGENCE, submission)
    normalized = " ".join(text.strip().split())
    if not normalized or normalized == "帮助":
        return Command(CommandKind.HELP)
    if normalized == "状态":
        return Command(CommandKind.STATUS)
    if normalized == "绑定本群":
        return Command(CommandKind.BIND)
    if normalized.startswith("绑定本群 "):
        return Command(CommandKind.BIND, normalized.removeprefix("绑定本群 "))
    if normalized == "AI测试":
        return Command(CommandKind.AI_TEST)
    if normalized.startswith("AI测试 "):
        return Command(CommandKind.AI_TEST, normalized.removeprefix("AI测试 "))
    if normalized == "策略":
        return Command(CommandKind.RISK_PROFILE)
    if normalized.startswith("策略 "):
        return Command(
            CommandKind.RISK_PROFILE,
            _profile_argument(normalized.removeprefix("策略 ")),
        )
    if normalized in _PROFILE_CHOICES:
        return Command(CommandKind.RISK_PROFILE, normalized)
    return Command(CommandKind.UNKNOWN, normalized)
