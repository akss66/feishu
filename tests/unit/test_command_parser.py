import pytest

from commerce_agent.command_parser import parse_command
from commerce_agent.domain import CommandKind


@pytest.mark.parametrize(
    ("text", "kind", "argument"),
    [
        ("帮助", CommandKind.HELP, ""),
        ("状态", CommandKind.STATUS, ""),
        ("绑定本群 abc123", CommandKind.BIND, "abc123"),
        ("AI测试 总结这句话", CommandKind.AI_TEST, "总结这句话"),
        ("随便聊聊", CommandKind.UNKNOWN, "随便聊聊"),
    ],
)
def test_parse_command(text: str, kind: CommandKind, argument: str) -> None:
    command = parse_command(text)

    assert command.kind is kind
    assert command.argument == argument
