# Feishu Bot Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a locally runnable Python service that connects to the published Feishu app over WebSocket, binds one test group, persists the binding in SQLite, handles basic commands, and verifies the official DeepSeek API without presenting test output as policy advice.

**Architecture:** Use a small modular monolith with dependency inversion at the application boundary. `BotService` owns command behavior and depends on repository and LLM protocols; SQLAlchemy, DeepSeek, and Feishu Channel SDK are adapters. The process uses one asyncio event loop and one WebSocket connection, with secrets loaded only from `.env`.

**Tech Stack:** Python 3.11.9, `lark-channel-sdk==1.2.0`, OpenAI Python SDK against DeepSeek, SQLAlchemy 2.0 async, SQLite via aiosqlite, Pydantic Settings 2, pytest, pytest-asyncio, Ruff.

## Global Constraints

- Never use the DeepSeek key previously pasted into chat; the operator must create and enter a replacement key locally.
- Never place App Secret, API keys, binding codes, authorization headers, or tokens in source, tests, logs, shell commands, or Git.
- Use the already-approved Feishu permissions only: `im:message:send_as_bot`, `im:message.group_at_msg:readonly`, `im:message.p2p_msg:readonly`.
- Consume only the already-configured event `im.message.receive_v1`.
- Use `https://api.deepseek.com` and model `deepseek-v4-pro`.
- Use `lark_channel.FeishuChannel` rather than the legacy `lark_oapi.channel` import.
- Run one process and one Feishu WebSocket connection in this milestone.
- All user-visible bot responses are Simplified Chinese.
- `AI测试` output must state that it is a connectivity test and not a platform-policy conclusion.
- Every behavior change follows red-green-refactor TDD and ends in an atomic commit.

---

## File Structure

```text
.
├── .env.example                         # Secret-free local configuration template
├── .gitignore                           # Already present; protects .env and local DB files
├── pyproject.toml                       # Runtime, development dependencies, pytest/Ruff config
├── README.md                            # Exact setup and Feishu manual test instructions
├── src/
│   └── commerce_agent/
│       ├── __init__.py                  # Package metadata only
│       ├── __main__.py                  # `python -m commerce_agent` entry point
│       ├── config.py                    # Validated environment settings
│       ├── domain.py                    # Inbound message and command value objects
│       ├── command_parser.py            # Chinese command parsing only
│       ├── application.py               # BotService and adapter protocols
│       ├── runtime.py                   # Dependency composition and lifecycle
│       ├── persistence/
│       │   ├── __init__.py
│       │   ├── database.py              # Async SQLAlchemy engine/session lifecycle
│       │   ├── models.py                # GroupBinding table
│       │   └── group_bindings.py        # Binding repository adapter
│       └── integrations/
│           ├── __init__.py
│           ├── deepseek.py              # DeepSeek LLM adapter
│           └── feishu.py                # Feishu Channel SDK adapter
└── tests/
    ├── unit/
    │   ├── test_config.py
    │   ├── test_command_parser.py
    │   ├── test_application.py
    │   ├── test_deepseek.py
    │   └── test_feishu.py
    └── integration/
        └── test_group_bindings.py
```

---

### Task 1: Project Packaging and Secret-Safe Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/commerce_agent/__init__.py`
- Create: `src/commerce_agent/config.py`
- Create: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `commerce_agent.config.Settings`
- Produces fields: `lark_app_id`, `lark_app_secret`, `deepseek_api_key`, `deepseek_base_url`, `deepseek_model`, `deepseek_timeout_seconds`, `bot_bind_code`, `database_url`, `log_level`
- Consumes: environment variables only; no caller passes secret literals.

- [ ] **Step 1: Add the package manifest and test configuration**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "cross-border-commerce-agent"
version = "0.1.0"
description = "Feishu cross-border ecommerce intelligence agent"
requires-python = ">=3.11,<3.13"
dependencies = [
  "aiosqlite>=0.21,<1",
  "lark-channel-sdk==1.2.0",
  "openai>=1.60,<3",
  "pydantic-settings>=2.7,<3",
  "sqlalchemy[asyncio]>=2.0.51,<2.2",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3,<10",
  "pytest-asyncio>=0.25,<2",
  "pytest-cov>=6,<8",
  "ruff>=0.11,<1",
]

[tool.hatch.build.targets.wheel]
packages = ["src/commerce_agent"]

[tool.pytest.ini_options]
addopts = "-q --strict-markers"
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC"]
```

Create `.env.example` with blank secrets:

```dotenv
LARK_APP_ID=
LARK_APP_SECRET=
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_TIMEOUT_SECONDS=30
BOT_BIND_CODE=
DATABASE_URL=sqlite+aiosqlite:///./commerce_agent.db
LOG_LEVEL=INFO
```

- [ ] **Step 2: Install the editable package and development dependencies**

Run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Expected: all packages install successfully and the final line contains `Successfully installed cross-border-commerce-agent`.

- [ ] **Step 3: Write failing settings tests**

Create `tests/unit/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from commerce_agent.config import Settings


def test_settings_load_required_secrets_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LARK_APP_ID", "cli_test")
    monkeypatch.setenv("LARK_APP_SECRET", "local-test-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-key")
    monkeypatch.setenv("BOT_BIND_CODE", "local-bind-code")

    settings = Settings(_env_file=None)

    assert settings.lark_app_id == "cli_test"
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-v4-pro"
    assert settings.lark_app_secret.get_secret_value() == "local-test-secret"


def test_settings_reject_blank_required_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LARK_APP_ID", "cli_test")
    monkeypatch.setenv("LARK_APP_SECRET", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-key")
    monkeypatch.setenv("BOT_BIND_CODE", "local-bind-code")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
```

- [ ] **Step 4: Run the settings test and confirm red**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'commerce_agent.config'`.

- [ ] **Step 5: Implement validated settings**

Create `src/commerce_agent/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `src/commerce_agent/config.py`:

```python
from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    lark_app_id: str = Field(min_length=1)
    lark_app_secret: SecretStr
    deepseek_api_key: SecretStr
    deepseek_base_url: HttpUrl = HttpUrl("https://api.deepseek.com")
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    bot_bind_code: SecretStr
    database_url: str = "sqlite+aiosqlite:///./commerce_agent.db"
    log_level: str = "INFO"

    @field_validator("lark_app_secret", "deepseek_api_key", "bot_bind_code")
    @classmethod
    def reject_blank_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("secret value must not be blank")
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return normalized
```

- [ ] **Step 6: Run tests and lint**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Expected: two tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 7: Commit the configuration slice**

```powershell
git add pyproject.toml .env.example src/commerce_agent/__init__.py src/commerce_agent/config.py tests/unit/test_config.py
git commit -m "chore: bootstrap secret-safe Python configuration"
```

---

### Task 2: Persist a Single Active Feishu Group Binding

**Files:**
- Create: `src/commerce_agent/persistence/__init__.py`
- Create: `src/commerce_agent/persistence/database.py`
- Create: `src/commerce_agent/persistence/models.py`
- Create: `src/commerce_agent/persistence/group_bindings.py`
- Create: `tests/integration/test_group_bindings.py`

**Interfaces:**
- Consumes: `database_url: str`
- Produces: `Database.create_schema()`, `Database.session()` and `Database.dispose()`
- Produces: `SqlAlchemyGroupBindingStore.bind(chat_id: str) -> None`
- Produces: `SqlAlchemyGroupBindingStore.get_active_chat_id() -> str | None`
- Produces: `SqlAlchemyGroupBindingStore.is_active(chat_id: str) -> bool`

- [ ] **Step 1: Write the failing repository integration test**

Create `tests/integration/test_group_bindings.py`:

```python
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.group_bindings import SqlAlchemyGroupBindingStore


async def test_binding_a_new_group_deactivates_the_previous_group(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await database.create_schema()
    store = SqlAlchemyGroupBindingStore(database.session)

    await store.bind("chat-one")
    await store.bind("chat-two")

    assert await store.is_active("chat-one") is False
    assert await store.is_active("chat-two") is True
    assert await store.get_active_chat_id() == "chat-two"
    await database.dispose()
```

- [ ] **Step 2: Run the repository test and confirm red**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_group_bindings.py -v
```

Expected: collection fails because `commerce_agent.persistence` does not exist.

- [ ] **Step 3: Implement the database lifecycle and model**

Create `src/commerce_agent/persistence/__init__.py` as an empty file.

Create `src/commerce_agent/persistence/database.py`:

```python
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from commerce_agent.persistence.models import Base


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
```

Create `src/commerce_agent/persistence/models.py`:

```python
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class GroupBinding(Base):
    __tablename__ = "group_bindings"

    chat_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 4: Implement the binding repository**

Create `src/commerce_agent/persistence/group_bindings.py`:

```python
from collections.abc import Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from commerce_agent.persistence.models import GroupBinding


class SqlAlchemyGroupBindingStore:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def bind(self, chat_id: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(update(GroupBinding).values(active=False))
                binding = await session.get(GroupBinding, chat_id)
                if binding is None:
                    session.add(GroupBinding(chat_id=chat_id, active=True))
                else:
                    binding.active = True

    async def get_active_chat_id(self) -> str | None:
        async with self._session_factory() as session:
            result = await session.scalar(
                select(GroupBinding.chat_id).where(GroupBinding.active.is_(True)).limit(1)
            )
            return result

    async def is_active(self, chat_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.scalar(
                select(GroupBinding.active).where(GroupBinding.chat_id == chat_id)
            )
            return result is True
```

- [ ] **Step 5: Run the repository test and full quality checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_group_bindings.py -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Expected: repository test passes and Ruff reports no errors.

- [ ] **Step 6: Commit the persistence slice**

```powershell
git add src/commerce_agent/persistence tests/integration/test_group_bindings.py
git commit -m "feat: persist the active Feishu group binding"
```

---

### Task 3: Parse the Chinese Bot Command Surface

**Files:**
- Create: `src/commerce_agent/domain.py`
- Create: `src/commerce_agent/command_parser.py`
- Create: `tests/unit/test_command_parser.py`

**Interfaces:**
- Produces: `InboundMessage(chat_id: str, message_id: str, text: str)`
- Produces: `CommandKind` values `HELP`, `STATUS`, `BIND`, `AI_TEST`, `UNKNOWN`
- Produces: `Command(kind: CommandKind, argument: str)`
- Produces: `parse_command(text: str) -> Command`

- [ ] **Step 1: Write failing parser tests**

Create `tests/unit/test_command_parser.py`:

```python
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
```

- [ ] **Step 2: Run the parser test and confirm red**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_command_parser.py -v
```

Expected: collection fails because `commerce_agent.command_parser` does not exist.

- [ ] **Step 3: Implement domain objects and parser**

Create `src/commerce_agent/domain.py`:

```python
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
```

Create `src/commerce_agent/command_parser.py`:

```python
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
```

- [ ] **Step 4: Run tests and lint**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_command_parser.py -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Expected: five parser cases pass and Ruff reports no errors.

- [ ] **Step 5: Commit the command surface**

```powershell
git add src/commerce_agent/domain.py src/commerce_agent/command_parser.py tests/unit/test_command_parser.py
git commit -m "feat: define the Feishu bot command surface"
```

---

### Task 4: Add a Tested DeepSeek Connectivity Adapter

**Files:**
- Create: `src/commerce_agent/integrations/__init__.py`
- Create: `src/commerce_agent/integrations/deepseek.py`
- Create: `tests/unit/test_deepseek.py`

**Interfaces:**
- Consumes: an `openai.AsyncOpenAI`-compatible client and model name.
- Produces: `DeepSeekGateway.answer_test(prompt: str) -> str`
- Raises: `RuntimeError("DeepSeek returned an empty response")` for empty model output.

- [ ] **Step 1: Write failing adapter tests with a fake OpenAI client**

Create `tests/unit/test_deepseek.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from commerce_agent.integrations.deepseek import DeepSeekGateway


async def test_answer_test_calls_the_configured_model() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="连接正常"))]
        )
    )
    gateway = DeepSeekGateway(client=client, model="deepseek-v4-pro")

    result = await gateway.answer_test("回复一句话")

    assert result == "连接正常"
    client.chat.completions.create.assert_awaited_once()
    request = client.chat.completions.create.await_args.kwargs
    assert request["model"] == "deepseek-v4-pro"
    assert request["messages"][0]["role"] == "system"


async def test_answer_test_rejects_empty_output() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
        )
    )
    gateway = DeepSeekGateway(client=client, model="deepseek-v4-pro")

    with pytest.raises(RuntimeError, match="empty response"):
        await gateway.answer_test("回复一句话")
```

- [ ] **Step 2: Run the adapter tests and confirm red**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_deepseek.py -v
```

Expected: collection fails because `commerce_agent.integrations.deepseek` does not exist.

- [ ] **Step 3: Implement the DeepSeek adapter**

Create `src/commerce_agent/integrations/__init__.py` as an empty file.

Create `src/commerce_agent/integrations/deepseek.py`:

```python
from typing import Any


class DeepSeekGateway:
    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    async def answer_test(self, prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你正在执行 API 连通性测试。只回答用户给出的测试问题，"
                        "不要将回答表述为任何电商平台的政策结论。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            stream=False,
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError("DeepSeek returned an empty response")
        return content.strip()
```

- [ ] **Step 4: Run tests and lint**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_deepseek.py -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Expected: both DeepSeek adapter tests pass and Ruff reports no errors.

- [ ] **Step 5: Commit the DeepSeek adapter**

```powershell
git add src/commerce_agent/integrations tests/unit/test_deepseek.py
git commit -m "feat: add DeepSeek connectivity adapter"
```

---

### Task 5: Implement Command Behavior with Dependency Inversion

**Files:**
- Create: `src/commerce_agent/application.py`
- Create: `tests/unit/test_application.py`

**Interfaces:**
- Consumes protocol: `GroupBindingStore.bind(chat_id)`, `get_active_chat_id()`, `is_active(chat_id)`
- Consumes protocol: `LLMGateway.answer_test(prompt)`
- Produces: `BotService.handle(message: InboundMessage) -> str`

- [ ] **Step 1: Write failing application tests**

Create `tests/unit/test_application.py`:

```python
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

    assert reply == "🧪 连通性测试（不作为平台政策依据）\n\n模型回复：你好"
```

- [ ] **Step 2: Run the application tests and confirm red**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_application.py -v
```

Expected: collection fails because `commerce_agent.application` does not exist.

- [ ] **Step 3: Implement the service and protocols**

Create `src/commerce_agent/application.py`:

```python
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
                "• 帮助\n"
                "• 状态\n"
                "• 绑定本群 <绑定码>\n"
                "• AI测试 <问题>"
            )
        if command.kind is CommandKind.STATUS:
            if await self._bindings.is_active(message.chat_id):
                return "✅ 当前群是已绑定的日报测试群。"
            active = await self._bindings.get_active_chat_id()
            return "ℹ️ 当前群未绑定。" if active else "ℹ️ 尚未绑定日报测试群。"
        if command.kind is CommandKind.BIND:
            if not command.argument or not compare_digest(command.argument, self._bind_code):
                return "❌ 绑定码不正确。"
            await self._bindings.bind(message.chat_id)
            return "✅ 已将当前群绑定为日报测试群。"
        if command.kind is CommandKind.AI_TEST:
            if not command.argument:
                return "请输入测试内容，例如：AI测试 用一句话介绍跨境电商。"
            answer = await self._llm.answer_test(command.argument)
            return f"🧪 连通性测试（不作为平台政策依据）\n\n{answer}"
        return "暂不支持该指令。发送“帮助”查看可用命令。"
```

- [ ] **Step 4: Run tests and lint**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_application.py -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Expected: three application tests pass and Ruff reports no errors.

- [ ] **Step 5: Commit the application service**

```powershell
git add src/commerce_agent/application.py tests/unit/test_application.py
git commit -m "feat: handle binding status and AI test commands"
```

---

### Task 6: Connect the Application Service to Feishu Channel SDK

**Files:**
- Create: `src/commerce_agent/integrations/feishu.py`
- Create: `tests/unit/test_feishu.py`

**Interfaces:**
- Consumes: a Channel-compatible object exposing `on`, `send`, and `connect`.
- Consumes: `BotService.handle(InboundMessage) -> str`.
- Produces: `FeishuAdapter.connect() -> None`.
- Feishu normalization fields used: `chat_id`, `message_id`, `content_text`.

- [ ] **Step 1: Write the failing adapter test**

Create `tests/unit/test_feishu.py`:

```python
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
        assert message.text == "帮助"
        return "帮助内容"


async def test_message_event_is_replied_to_in_thread() -> None:
    channel = FakeChannel()
    adapter = FeishuAdapter(channel, FakeService())
    event = SimpleNamespace(chat_id="chat-one", message_id="msg-one", content_text="帮助")

    await channel.handlers["message"](event)

    assert channel.sent == [
        ("chat-one", {"text": "帮助内容"}, {"reply_to": "msg-one"})
    ]
    await adapter.connect()
    assert channel.connected is True
```

- [ ] **Step 2: Run the adapter test and confirm red**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_feishu.py -v
```

Expected: collection fails because `commerce_agent.integrations.feishu` does not exist.

- [ ] **Step 3: Implement the Feishu adapter**

Create `src/commerce_agent/integrations/feishu.py`:

```python
from typing import Any

from commerce_agent.application import BotService
from commerce_agent.domain import InboundMessage


class FeishuAdapter:
    def __init__(self, channel: Any, service: BotService) -> None:
        self._channel = channel
        self._service = service
        self._channel.on("message", self._on_message)

    async def _on_message(self, event: Any) -> None:
        inbound = InboundMessage(
            chat_id=event.chat_id,
            message_id=event.message_id,
            text=event.content_text,
        )
        reply = await self._service.handle(inbound)
        await self._channel.send(
            inbound.chat_id,
            {"text": reply},
            {"reply_to": inbound.message_id},
        )

    async def connect(self) -> None:
        await self._channel.connect()
```

- [ ] **Step 4: Run tests and lint**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_feishu.py -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Expected: Feishu adapter test passes and Ruff reports no errors.

- [ ] **Step 5: Commit the Feishu adapter**

```powershell
git add src/commerce_agent/integrations/feishu.py tests/unit/test_feishu.py
git commit -m "feat: connect bot commands to Feishu messages"
```

---

### Task 7: Compose the Runtime and Document the Manual End-to-End Test

**Files:**
- Create: `src/commerce_agent/runtime.py`
- Create: `src/commerce_agent/__main__.py`
- Create: `README.md`
- Modify: `docs/superpowers/specs/2026-07-20-cross-border-ecommerce-feishu-agent-design.md`

**Interfaces:**
- Produces: `commerce_agent.runtime.run() -> None`.
- Produces command: `.\.venv\Scripts\python.exe -m commerce_agent`.
- Consumes all validated `Settings` fields and constructs concrete adapters.

- [ ] **Step 1: Add a composition smoke test before implementation**

Append to `tests/unit/test_config.py`:

```python
def test_secret_values_are_redacted_in_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LARK_APP_ID", "cli_test")
    monkeypatch.setenv("LARK_APP_SECRET", "do-not-print-this")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "do-not-print-that")
    monkeypatch.setenv("BOT_BIND_CODE", "do-not-print-code")

    rendered = repr(Settings(_env_file=None))

    assert "do-not-print-this" not in rendered
    assert "do-not-print-that" not in rendered
    assert "do-not-print-code" not in rendered
```

- [ ] **Step 2: Run the new safety test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py -v
```

Expected: all three settings tests pass because Pydantic `SecretStr` redacts values.

- [ ] **Step 3: Implement runtime composition**

Create `src/commerce_agent/runtime.py`:

```python
import logging

from lark_channel import FeishuChannel, SecurityConfig
from openai import AsyncOpenAI

from commerce_agent.application import BotService
from commerce_agent.config import Settings
from commerce_agent.integrations.deepseek import DeepSeekGateway
from commerce_agent.integrations.feishu import FeishuAdapter
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.group_bindings import SqlAlchemyGroupBindingStore


async def run() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    database = Database(settings.database_url)
    await database.create_schema()
    bindings = SqlAlchemyGroupBindingStore(database.session)

    openai_client = AsyncOpenAI(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=str(settings.deepseek_base_url).rstrip("/"),
        timeout=settings.deepseek_timeout_seconds,
    )
    llm = DeepSeekGateway(openai_client, settings.deepseek_model)
    service = BotService(bindings, llm, settings.bot_bind_code.get_secret_value())
    channel = FeishuChannel(
        app_id=settings.lark_app_id,
        app_secret=settings.lark_app_secret.get_secret_value(),
        security=SecurityConfig(mode="audit"),
    )
    adapter = FeishuAdapter(channel, service)

    try:
        await adapter.connect()
    finally:
        await openai_client.close()
        await database.dispose()
```

Create `src/commerce_agent/__main__.py`:

```python
import asyncio

from commerce_agent.runtime import run


if __name__ == "__main__":
    asyncio.run(run())
```

- [ ] **Step 4: Update the design binding rule to match the minimal-permission implementation**

In `docs/superpowers/specs/2026-07-20-cross-border-ecommerce-feishu-agent-design.md`, replace the sentence describing `@机器人 绑定本群` with:

```markdown
机器人首次进入测试群后，由管理员发送 `@机器人 绑定本群 <绑定码>`。绑定码只保存在服务端环境变量中，验证通过后系统保存事件中的 `chat_id`，并将该群设为唯一日报目标。重新绑定需要再次提供绑定码，避免误投递；这种方式无需额外申请群成员管理权限。
```

- [ ] **Step 5: Write the exact operator README**

Create `README.md`:

```markdown
# 跨境电商飞书情报智能体

当前里程碑提供飞书长连接、单群绑定、状态命令和 DeepSeek 连通性测试。

## 本地准备

1. 确认 Python 3.11 可用。
2. 创建虚拟环境并安装依赖：

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
   ```

3. 复制本地配置文件：

   ```powershell
   Copy-Item .env.example .env
   ```

4. 只在本地 `.env` 中填写：
   - `LARK_APP_ID`
   - `LARK_APP_SECRET`
   - 新创建的 `DEEPSEEK_API_KEY`
   - 自己生成的高强度 `BOT_BIND_CODE`

不要把 `.env`、密钥或绑定码提交到 Git 或发送到聊天中。

## 启动

```powershell
.\.venv\Scripts\python.exe -m commerce_agent
```

进程保持运行时，飞书开放平台的长连接状态应显示为已连接。

## 飞书测试

将机器人加入测试群，然后依次发送：

```text
@机器人 帮助
@机器人 绑定本群 <你在本地设置的绑定码>
@机器人 状态
@机器人 AI测试 用一句话说明跨境电商是什么
```

`AI测试` 仅验证 DeepSeek 连通性，不作为平台政策依据。

## 自动检查

```powershell
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe -m ruff check src tests
```
```

- [ ] **Step 6: Run the full automated verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe -m ruff check src tests
git status --short
```

Expected: all tests pass, Ruff reports `All checks passed!`, and Git lists only the runtime, README, spec, and safety-test changes intended for this task.

- [ ] **Step 7: Run the manual Feishu/DeepSeek end-to-end test**

The operator performs these secret-bearing steps locally; secrets must not be pasted into the implementation session:

1. Copy `.env.example` to `.env` and fill all four required secret values.
2. Start `.\.venv\Scripts\python.exe -m commerce_agent`.
3. Confirm the process logs a successful Feishu WebSocket connection without printing credentials.
4. Send `@机器人 帮助` and confirm a threaded Chinese reply.
5. Send `@机器人 绑定本群 <本地绑定码>` and confirm the success reply.
6. Send `@机器人 状态` and confirm the group is active.
7. Send `@机器人 AI测试 只回复“连接正常”` and confirm the labeled DeepSeek response.
8. Restart the process and send `@机器人 状态` again to prove SQLite persistence.

- [ ] **Step 8: Commit the runnable vertical slice**

```powershell
git add README.md src/commerce_agent/runtime.py src/commerce_agent/__main__.py tests/unit/test_config.py docs/superpowers/specs/2026-07-20-cross-border-ecommerce-feishu-agent-design.md
git commit -m "feat: run the Feishu and DeepSeek vertical slice"
```

---

## Milestone Completion Gate

Do not start source crawling or scheduled reports until all of these are true:

- The full pytest suite passes.
- Ruff passes.
- No credential values appear in `git diff --cached` or tracked files.
- Feishu shows an active WebSocket connection.
- The test group can be bound and remains bound after restart.
- `AI测试` returns a labeled DeepSeek response.
- The implementation matches the three approved permissions and single event subscription.

After this gate, write separate plans for:

1. source registry and public-source ingestion;
2. document snapshots, deduplication, and event aggregation;
3. structured DeepSeek analysis, risk scoring, and evidence binding;
4. 09:00 daily report, urgent alerts, and Feishu cards;
5. cloud deployment, health monitoring, retries, and PostgreSQL migration.
