import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from commerce_agent.integrations.deepseek import DeepSeekGateway
from commerce_agent.intelligence.analyzer import EmptyModelOutput


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
    assert "AI测试不代表任何平台政策结论" in request["messages"][0]["content"]
    assert request["messages"][1] == {"role": "user", "content": "回复一句话"}
    assert request["stream"] is False


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


async def test_complete_json_serializes_the_payload_without_ascii_escaping() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  {\"ok\":true}  "))]
        )
    )
    gateway = DeepSeekGateway(client=client, model="deepseek-v4-pro")

    result = await gateway.complete_json("只输出 JSON", {"article": {"title": "费用"}})

    assert result == '{"ok":true}'
    request = client.chat.completions.create.await_args.kwargs
    assert request == {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "只输出 JSON"},
            {
                "role": "user",
                "content": json.dumps(
                    {"article": {"title": "费用"}},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ],
        "stream": False,
    }


async def test_complete_json_rejects_whitespace_only_output() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  \n "))]
        )
    )
    gateway = DeepSeekGateway(client=client, model="deepseek-v4-pro")

    with pytest.raises(EmptyModelOutput, match="empty response"):
        await gateway.complete_json("只输出 JSON", {"article": {}})
