import pytest

from commerce_agent.command_parser import parse_command
from commerce_agent.domain import CommandKind


@pytest.mark.parametrize(
    ("text", "kind", "argument"),
    [
        ("", CommandKind.HELP, ""),
        ("   \n\t", CommandKind.HELP, ""),
        ("帮助", CommandKind.HELP, ""),
        ("状态", CommandKind.STATUS, ""),
        ("绑定本群 abc123", CommandKind.BIND, "abc123"),
        ("AI测试 总结这句话", CommandKind.AI_TEST, "总结这句话"),
        ("策略", CommandKind.RISK_PROFILE, ""),
        ("策略 保守", CommandKind.RISK_PROFILE, "保守"),
        ("策略 默认", CommandKind.RISK_PROFILE, "默认"),
        ("策略 激进", CommandKind.RISK_PROFILE, "激进"),
        ("策略激进", CommandKind.UNKNOWN, "策略激进"),
        ("随便聊聊", CommandKind.UNKNOWN, "随便聊聊"),
    ],
)
def test_parse_command(text: str, kind: CommandKind, argument: str) -> None:
    command = parse_command(text)

    assert command.kind is kind
    assert command.argument == argument
