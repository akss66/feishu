import hashlib
import json
import logging
import unicodedata
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import commerce_agent
from commerce_agent.intelligence.delivery import (
    DeliverySendError,
    DeliveryWorker,
    FeishuDeliveryPort,
    FeishuMessageRenderer,
    _plain,
    safe_feishu_error_code,
)
from commerce_agent.intelligence.models import DeliveryClaim, MessageKind

NOW = datetime(2026, 7, 21, 1, tzinfo=UTC)


def test_pytest_imports_commerce_agent_from_current_worktree() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    expected = (repository_root / "src" / "commerce_agent" / "__init__.py").resolve()

    assert Path(commerce_agent.__file__).resolve() == expected


def _alert_item(
    *,
    risk_level: str = "high",
    confidence: int = 91,
    profile: str = "default",
    status: str = "verified",
    suffix: str = "",
) -> dict[str, object]:
    return {
        "risk_level": risk_level,
        "evidence_confidence": confidence,
        "risk_profile": profile,
        "verification_status": status,
        "headline": f"平台规则更新{suffix}",
        "summary": "平台发布了会影响跨境卖家的规则更新。",
        "impact": "相关商品需要在生效日前完成合规检查。",
        "rationale": [{"claim": "规则适用于目标卖家", "quote": "本规则适用于跨境卖家"}],
        "actions": [
            {
                "action": "核对受影响商品",
                "owner_type": "运营负责人",
                "deadline": "2026-07-22T00:00:00Z",
            }
        ],
        "uncertainties": ["部分地区的适用范围仍待确认"],
        "source_name": f"平台公告{suffix}",
        "source_url": f"https://example.com/source/{suffix or 'one'}",
    }


def _claim(
    *,
    kind: MessageKind = MessageKind.HIGH_ALERT,
    payload: dict[str, object] | None = None,
    group_id: str = "chat-one",
    reply_to: str | None = None,
    reply_in_thread: bool = False,
) -> DeliveryClaim:
    return DeliveryClaim(
        id=1,
        idempotency_key="delivery-key-one",
        group_id=group_id,
        kind=kind,
        payload=payload
        or {
            "title": "高风险预警",
            "theme": "red",
            "items": [_alert_item()],
        },
        reply_to_message_id=reply_to,
        reply_in_thread=reply_in_thread,
        attempt_count=1,
        lease_token="lease-one",
    )


def test_daily_card_prefers_evidence_rich_items_when_updates_exist() -> None:
    item = _alert_item(confidence=70, status="early_signal")
    claim = _claim(
        kind=MessageKind.DAILY_REPORT,
        payload={
            "title": "Daily intelligence",
            "theme": "blue",
            "risk_profile": "default",
            "sections": [{"title": "Summary", "items": ["One pending update"]}],
            "items": [item],
        },
    )

    rendered = FeishuMessageRenderer().render(claim)
    encoded = json.dumps(rendered, ensure_ascii=False)

    assert "One pending update" not in encoded
    assert item["headline"] in encoded
    assert item["summary"] in encoded
    assert item["rationale"][0]["quote"] in encoded
    assert item["actions"][0]["action"] in encoded
    assert item["source_url"] in encoded
    assert rendered["card"]["header"]["template"] == "blue"
    first_element = rendered["card"]["elements"][0]
    assert first_element["tag"] == "div"
    assert first_element["text"]["tag"] == "lark_md"
    assert isinstance(first_element["text"]["content"], str)


@pytest.mark.parametrize(
    ("kind", "payload", "expected_theme"),
    [
        (
            MessageKind.HIGH_ALERT,
            {"title": "高风险预警", "theme": "red", "items": [_alert_item()]},
            "red",
        ),
        (
            MessageKind.MEDIUM_ALERT_BATCH,
            {
                "title": "中风险预警汇总",
                "theme": "orange",
                "items": [_alert_item(risk_level="medium")],
            },
            "orange",
        ),
        (
            MessageKind.DAILY_REPORT,
            {
                "title": "跨境电商每日情报 · 2026-07-21 · 策略：默认",
                "theme": "blue",
                "risk_profile": "default",
                "sections": [{"title": "AI 今日提炼", "items": ["没有新事项"]}],
            },
            "blue",
        ),
    ],
)
def test_renderer_preserves_verified_alert_and_daily_themes(
    kind: MessageKind, payload: dict[str, object], expected_theme: str
) -> None:
    rendered = FeishuMessageRenderer().render(_claim(kind=kind, payload=payload))

    assert rendered["card"]["header"]["template"] == expected_theme


def test_alert_card_contains_decision_fields_and_source_link() -> None:
    rendered = FeishuMessageRenderer().render(_claim())
    encoded = json.dumps(rendered, ensure_ascii=False)

    for expected in (
        "关注程度：高（建议尽快处理）",
        "信息可靠度：很高（91/100）",
        "一句话看懂：平台发布了会影响跨境卖家的规则更新。",
        "对店铺可能有什么影响：相关商品需要在生效日前完成合规检查。",
        "建议你这样做：",
        "1. 核对受影响商品（负责人：运营负责人；建议完成：2026-07-22）",
        "为什么这样判断：规则适用于目标卖家（原文依据：本规则适用于跨境卖家）",
        "目前还不确定：部分地区的适用范围仍待确认",
        "分析策略：默认｜信息状态：已有可靠原文支持",
        "查看原文：[平台公告](https://example.com/source/one)",
    ):
        assert expected in encoded

    for professional_label in ("风险：high", "证据可信度：", "建议动作：", "不确定性："):
        assert professional_label not in encoded


def test_alert_card_rejects_markdown_injection_in_source_url() -> None:
    item = _alert_item()
    item["source_url"] = "https://example.com) javascript:alert(1)"
    claim = _claim(payload={"title": "高风险预警", "theme": "red", "items": [item]})

    encoded = json.dumps(FeishuMessageRenderer().render(claim), ensure_ascii=False)

    assert "链接不可用" in encoded
    assert "javascript:" not in encoded


def test_early_signal_is_forced_orange_even_when_payload_requests_red() -> None:
    claim = _claim(
        payload={
            "title": "错误的红色早期预警",
            "theme": "red",
            "items": [
                _alert_item(
                    risk_level="high",
                    confidence=60,
                    profile="aggressive",
                    status="early_signal",
                )
            ],
        }
    )

    rendered = FeishuMessageRenderer().render(claim)
    encoded = json.dumps(rendered, ensure_ascii=False)

    assert rendered["card"]["header"]["template"] == "orange"
    assert "初步线索，暂勿直接调整业务" in encoded
    assert "激进" in encoded


def test_daily_title_always_displays_strategy_profile() -> None:
    claim = _claim(
        kind=MessageKind.DAILY_REPORT,
        payload={
            "title": "跨境电商每日情报 · 2026-07-21",
            "theme": "red",
            "risk_profile": "conservative",
            "sections": [{"title": "AI 今日提炼", "items": ["没有新事项"]}],
        },
    )

    rendered = FeishuMessageRenderer().render(claim)

    assert rendered["card"]["header"]["template"] == "blue"
    assert "策略：保守" in rendered["card"]["header"]["title"]["content"]


@pytest.mark.parametrize(
    "character",
    [*(chr(value) for value in range(0x20)), *(chr(value) for value in range(0x7F, 0xA0))],
)
def test_plain_sanitizes_c0_and_c1_controls_before_markdown_escaping(
    character: str,
) -> None:
    expected_separator = " " if character.isspace() else ""

    rendered = _plain(f"中{character}文🙂")

    assert rendered == f"中{expected_separator}文🙂"
    assert all(unicodedata.category(value) != "Cc" for value in rendered)


def test_daily_card_sanitizes_untrusted_controls_and_preserves_unicode() -> None:
    claim = _claim(
        kind=MessageKind.DAILY_REPORT,
        payload={
            "title": "跨境\n电商每日情报\x00🙂",
            "theme": "blue",
            "risk_profile": "default",
            "sections": [
                {
                    "title": "AI\t今日\x1b提炼🚀",
                    "items": ["中文\r\n内容\x85继续\x7f🙂"],
                }
            ],
        },
    )

    rendered = FeishuMessageRenderer().render(claim)

    assert rendered["card"]["header"]["title"]["content"] == (
        "跨境 电商每日情报🙂 · 策略：默认"
    )
    assert rendered["card"]["elements"][0]["text"]["content"] == (
        "**AI 今日提炼🚀**\n- 中文 内容 继续🙂"
    )


def test_alert_card_sanitizes_untrusted_controls_and_preserves_unicode() -> None:
    item = _alert_item()
    item.update(
        {
            "headline": "平台\x00规则\n更新🙂",
            "summary": "中文\r\n摘要\t保留🚀\x1b",
            "impact": "影响\x85说明\x7f",
            "rationale": [{"claim": "判\x01断", "quote": "原\n文"}],
            "actions": [
                {
                    "action": "核对\x00商品",
                    "owner_type": "运\t营",
                    "deadline": "2026-07-22\x1b",
                }
            ],
            "uncertainties": ["范围\r待确认\x7f"],
            "source_name": "平台\x00公告🙂",
        }
    )
    claim = _claim(
        payload={
            "title": "高\n风险\t预警\x00\x1b\x7f🙂",
            "theme": "red",
            "items": [item],
        }
    )

    rendered = FeishuMessageRenderer().render(claim)
    content = rendered["card"]["elements"][0]["text"]["content"]

    assert rendered["card"]["header"]["title"]["content"] == "高 风险 预警🙂"
    for expected in (
        "**平台规则 更新🙂**",
        "一句话看懂：中文 摘要 保留🚀",
        "对店铺可能有什么影响：影响 说明",
        "为什么这样判断：判断（原文依据：原 文）",
        "1. 核对商品（负责人：运 营；建议完成：2026-07-22）",
        "目前还不确定：范围 待确认",
        "查看原文：[平台公告🙂](https://example.com/source/one)",
    ):
        assert expected in content
    assert not any(character in content for character in ("\x00", "\x1b", "\x7f", "\r", "\t"))


def test_oversized_alert_card_degrades_to_text_with_at_most_15_linked_items() -> None:
    payload = {
        "title": "中风险预警汇总",
        "theme": "orange",
        "items": [
            {
                **_alert_item(
                    risk_level="medium",
                    suffix=str(index),
                ),
                "summary": "界" * 3000,
            }
            for index in range(20)
        ],
    }

    rendered = FeishuMessageRenderer().render(_claim(payload=payload))

    assert set(rendered) == {"text"}
    assert len(rendered["text"].encode("utf-8")) <= 20_000
    assert rendered["text"].count("原文：[平台公告") == 15
    assert "https://example.com/source/14" in rendered["text"]
    assert "https://example.com/source/15" not in rendered["text"]
    assert "策略：默认" in rendered["text"]
    assert "信息状态：已有可靠原文支持" in rendered["text"]


def test_oversized_daily_card_degrades_to_utf8_bounded_text() -> None:
    claim = _claim(
        kind=MessageKind.DAILY_REPORT,
        payload={
            "title": "跨境电商每日情报 · 2026-07-21",
            "theme": "blue",
            "risk_profile": "aggressive",
            "sections": [
                {
                    "title": "数据覆盖与来源",
                    "items": [
                        f"来源{index}｜https://example.com/{index}｜{'界' * 3000}"
                        for index in range(20)
                    ],
                }
            ],
        },
    )

    rendered = FeishuMessageRenderer().render(claim)

    assert set(rendered) == {"text"}
    assert len(rendered["text"].encode("utf-8")) <= 20_000
    assert "策略：激进" in rendered["text"]
    assert "https://example.com/0" in rendered["text"]
    assert "https://example.com/15" not in rendered["text"]


def test_text_fallback_never_truncates_an_alert_before_its_source_link() -> None:
    items = []
    for index in range(15):
        item = _alert_item(risk_level="medium", suffix=str(index))
        item["actions"] = [
            {
                "action": f"核对受影响商品{action_index}",
                "owner_type": "运营负责人",
                "deadline": None,
            }
            for action_index in range(30)
        ]
        items.append(item)
    claim = _claim(
        kind=MessageKind.MEDIUM_ALERT_BATCH,
        payload={"title": "中风险预警汇总", "theme": "orange", "items": items},
    )

    rendered = FeishuMessageRenderer().render(claim)

    assert set(rendered) == {"text"}
    assert rendered["text"].count("**平台规则更新") == rendered["text"].count("原文：[平台公告")


def test_qa_answer_is_validated_as_plain_text_without_card_conversion() -> None:
    payload = {"text": "有据回答\n原文：https://example.com/evidence"}
    claim = _claim(kind=MessageKind.QA_ANSWER, payload=payload)

    rendered = FeishuMessageRenderer().render(claim)

    assert rendered == payload
    assert rendered is not payload


def test_qa_answer_accepts_text_at_utf8_limit() -> None:
    text = f"{'界' * 6666}aa"
    assert len(text.encode("utf-8")) == 20_000

    rendered = FeishuMessageRenderer().render(
        _claim(kind=MessageKind.QA_ANSWER, payload={"text": text})
    )

    assert rendered == {"text": text}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"card": {}},
        {"text": "回答", "card": {}},
        {"text": {"nested": "回答"}},
        {"text": 123},
        {"text": "x" * 20_001},
        {"text": "界" * 6667},
    ],
)
async def test_qa_answer_rejects_non_text_or_oversized_payload_as_format_error(
    payload: dict[str, object],
) -> None:
    claim = replace(
        _claim(kind=MessageKind.QA_ANSWER, payload={"text": "valid"}),
        payload=payload,
    )
    channel = FakeChannel()

    with pytest.raises(DeliverySendError) as raised:
        await FeishuDeliveryPort(channel, FeishuMessageRenderer()).send(claim)

    assert raised.value.code == "format_error"
    assert channel.sent == []


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, object], dict[str, object]]] = []
        self.result = SimpleNamespace(success=True, message_id="om_123", error=None)
        self.error: Exception | None = None

    async def send(
        self, group_id: str, message: dict[str, object], options: dict[str, object]
    ) -> object:
        self.sent.append((group_id, message, options))
        if self.error is not None:
            raise self.error
        return self.result


async def test_port_sends_proactively_with_stable_uuid() -> None:
    channel = FakeChannel()
    claim = _claim()

    message_id = await FeishuDeliveryPort(channel, FeishuMessageRenderer()).send(claim)

    assert message_id == "om_123"
    assert channel.sent == [
        (
            "chat-one",
            FeishuMessageRenderer().render(claim),
            {
                "reply_to": None,
                "reply_in_thread": False,
                "uuid": hashlib.sha256(b"delivery-key-one").hexdigest()[:32],
            },
        )
    ]


async def test_port_falls_back_to_text_when_feishu_rejects_card_shape() -> None:
    class CardRejectingChannel(FakeChannel):
        async def send(
            self,
            group_id: str,
            message: dict[str, object],
            options: dict[str, object],
        ) -> object:
            self.sent.append((group_id, message, options))
            if len(self.sent) == 1:
                return SimpleNamespace(
                    success=False,
                    message_id=None,
                    error=SimpleNamespace(
                        code=SimpleNamespace(value="unknown"),
                        raw_code=99992402,
                    ),
                )
            return SimpleNamespace(success=True, message_id="om_text", error=None)

    channel = CardRejectingChannel()
    claim = _claim(kind=MessageKind.DAILY_REPORT)

    message_id = await FeishuDeliveryPort(channel, FeishuMessageRenderer()).send(claim)

    assert message_id == "om_text"
    assert "card" in channel.sent[0][1]
    assert "text" in channel.sent[1][1]
    assert "原文：" in str(channel.sent[1][1]["text"])
    assert channel.sent[1][2]["uuid"] != claim.idempotency_key


async def test_port_sends_thread_reply_options() -> None:
    channel = FakeChannel()
    claim = _claim(reply_to="om_parent", reply_in_thread=True)

    await FeishuDeliveryPort(channel, FeishuMessageRenderer()).send(claim)

    assert channel.sent[0][2] == {
        "reply_to": "om_parent",
        "reply_in_thread": True,
        "uuid": hashlib.sha256(b"delivery-key-one").hexdigest()[:32],
    }


@pytest.mark.parametrize(
    ("sdk_code", "safe_code"),
    [
        ("rate_limited", "rate_limited"),
        ("permission_denied", "permission_denied"),
        ("target_revoked", "permission_denied"),
        ("format_error", "format_error"),
        ("send_timeout", "transport_error"),
        ("not_connected", "transport_error"),
        ("arbitrary-secret-code", "unknown_feishu_error"),
    ],
)
def test_feishu_error_codes_are_reduced_to_whitelist(sdk_code: str, safe_code: str) -> None:
    error = SimpleNamespace(code=SimpleNamespace(value=sdk_code))

    assert safe_feishu_error_code(error) == safe_code


def test_feishu_field_validation_code_maps_to_format_error() -> None:
    error = SimpleNamespace(
        code=SimpleNamespace(value="unknown"),
        raw_code=99992402,
    )

    assert safe_feishu_error_code(error) == "format_error"


async def test_port_converts_failed_result_without_logging_sensitive_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    channel = FakeChannel()
    channel.result = SimpleNamespace(
        success=False,
        message_id=None,
        error=SimpleNamespace(
            code=SimpleNamespace(value="send_timeout"),
            hint="secret remediation hint",
        ),
        raw={"token": "secret-token"},
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(DeliverySendError) as raised:
        await FeishuDeliveryPort(channel, FeishuMessageRenderer()).send(
            _claim(payload={"title": "secret payload", "theme": "red", "items": []})
        )

    assert raised.value.code == "transport_error"
    assert str(raised.value) == "transport_error"
    assert "secret" not in repr(raised.value)
    for forbidden in (
        "secret remediation hint",
        "secret-token",
        "secret payload",
        "chat-one",
    ):
        assert forbidden not in caplog.text


async def test_port_converts_raised_sdk_error_to_safe_code() -> None:
    channel = FakeChannel()
    channel.error = RuntimeError("raw transport response with secret-token")

    with pytest.raises(DeliverySendError) as raised:
        await FeishuDeliveryPort(channel, FeishuMessageRenderer()).send(_claim())

    assert raised.value.code == "unknown_feishu_error"
    assert "secret-token" not in repr(raised.value)


async def test_success_without_message_id_is_unknown_safe_failure() -> None:
    channel = FakeChannel()
    channel.result = SimpleNamespace(success=True, message_id=None, error=None)

    with pytest.raises(DeliverySendError) as raised:
        await FeishuDeliveryPort(channel, FeishuMessageRenderer()).send(_claim())

    assert raised.value.code == "unknown_feishu_error"


async def test_renderer_failure_is_reduced_to_format_error_before_sdk_call() -> None:
    channel = FakeChannel()

    class FailingRenderer:
        def render(self, claim: DeliveryClaim) -> dict[str, object]:
            raise ValueError("malformed payload containing secret-token")

    with pytest.raises(DeliverySendError) as raised:
        await FeishuDeliveryPort(channel, FailingRenderer()).send(_claim())

    assert raised.value.code == "format_error"
    assert "secret-token" not in repr(raised.value)
    assert channel.sent == []


class FakeRepository:
    def __init__(self, claims: list[DeliveryClaim]) -> None:
        self.claims = claims
        self.claimed_at: list[datetime] = []
        self.marked: list[tuple[DeliveryClaim, str, datetime]] = []
        self.failed: list[tuple[DeliveryClaim, str, datetime]] = []
        self.skipped: list[tuple[DeliveryClaim, str]] = []

    async def claim_delivery(self, *, now: datetime) -> DeliveryClaim | None:
        self.claimed_at.append(now)
        return self.claims.pop(0) if self.claims else None

    async def claim_delivery_by_id(self, outbox_id: int, *, now: datetime) -> DeliveryClaim | None:
        self.claimed_at.append(now)
        if not self.claims or self.claims[0].id != outbox_id:
            return None
        return self.claims.pop(0)

    async def mark_delivery_sent(
        self, claim: DeliveryClaim, *, message_id: str, now: datetime
    ) -> None:
        self.marked.append((claim, message_id, now))

    async def fail_delivery(self, claim: DeliveryClaim, code: str, *, now: datetime) -> None:
        self.failed.append((claim, code, now))

    async def skip_delivery(self, claim: DeliveryClaim, code: str) -> None:
        self.skipped.append((claim, code))


class FakeBindingStore:
    def __init__(self, active: bool) -> None:
        self.active = active
        self.checked: list[str] = []

    async def is_active(self, group_id: str) -> bool:
        self.checked.append(group_id)
        return self.active


async def test_worker_claims_sends_and_marks_message_id() -> None:
    claim = _claim()
    repository = FakeRepository([claim])
    channel = FakeChannel()
    worker = DeliveryWorker(
        repository,
        FeishuDeliveryPort(channel, FeishuMessageRenderer()),
        bindings=FakeBindingStore(True),
        clock=lambda: NOW,
    )

    summary = await worker.drain(limit=10)

    assert summary.sent == 1
    assert summary.failed == 0
    assert summary.skipped == 0
    assert repository.marked == [(claim, "om_123", NOW)]
    assert channel.sent[0][1] == FeishuMessageRenderer().render(claim)


async def test_worker_failure_reuses_claim_payload_without_analyzer() -> None:
    claim = _claim()
    repository = FakeRepository([claim])

    class FailingPort:
        def __init__(self) -> None:
            self.claims: list[DeliveryClaim] = []

        async def send(self, delivery_claim: DeliveryClaim) -> str:
            self.claims.append(delivery_claim)
            raise DeliverySendError("transport_error")

    port = FailingPort()
    worker = DeliveryWorker(
        repository,
        port,
        bindings=FakeBindingStore(True),
        clock=lambda: NOW,
    )

    summary = await worker.drain(limit=1)

    assert summary.failed == 1
    assert port.claims == [claim]
    assert port.claims[0].payload is claim.payload
    assert repository.failed == [(claim, "transport_error", NOW)]
    assert repository.marked == []


@pytest.mark.parametrize(
    ("claim", "bindings"),
    [
        (_claim(group_id=""), FakeBindingStore(True)),
        (_claim(), FakeBindingStore(False)),
    ],
)
async def test_worker_skips_missing_or_inactive_binding(
    claim: DeliveryClaim, bindings: FakeBindingStore
) -> None:
    repository = FakeRepository([claim])
    channel = FakeChannel()
    worker = DeliveryWorker(
        repository,
        FeishuDeliveryPort(channel, FeishuMessageRenderer()),
        bindings=bindings,
        clock=lambda: NOW,
    )

    summary = await worker.drain(limit=1)

    assert summary.skipped == 1
    assert repository.skipped == [(claim, "no_active_binding")]
    assert channel.sent == []


async def test_send_id_returns_empty_summary_when_claim_is_not_due() -> None:
    worker = DeliveryWorker(
        FakeRepository([]),
        FeishuDeliveryPort(FakeChannel(), FeishuMessageRenderer()),
        bindings=FakeBindingStore(True),
        clock=lambda: NOW,
    )

    summary = await worker.send_id(99)

    assert summary.sent == summary.failed == summary.skipped == 0


async def test_worker_passes_exact_claim_to_port() -> None:
    original = _claim()
    retry = replace(original, attempt_count=2, lease_token="lease-two")
    repository = FakeRepository([retry])

    class RecordingPort:
        def __init__(self) -> None:
            self.claim: DeliveryClaim | None = None

        async def send(self, claim: DeliveryClaim) -> str:
            self.claim = claim
            return "om_retry"

    port = RecordingPort()
    worker = DeliveryWorker(
        repository,
        port,
        bindings=FakeBindingStore(True),
        clock=lambda: NOW,
    )

    await worker.drain(limit=1)

    assert port.claim is retry
    assert port.claim.payload is original.payload
