from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class InboundMessage:
    chat_id: str
    message_id: str
    text: str


class CommandKind(StrEnum):
    HELP = "help"
    STATUS = "status"
    BIND = "bind"
    AI_TEST = "ai_test"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Command:
    kind: CommandKind
    argument: str = ""
