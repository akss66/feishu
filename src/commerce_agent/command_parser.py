from commerce_agent.domain import Command, CommandKind


def parse_command(text: str) -> Command:
    normalized = " ".join(text.strip().split())
    if normalized == "帮助":
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
    return Command(CommandKind.UNKNOWN, normalized)
