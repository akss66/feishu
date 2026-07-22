from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from unicodedata import category
from urllib.parse import quote, urlsplit, urlunsplit

from commerce_agent.intelligence.models import DeliveryClaim, MessageKind

CARD_UTF8_LIMIT = 28_000
TEXT_UTF8_LIMIT = 20_000
MAX_FALLBACK_ITEMS = 15

_SAFE_FEISHU_ERROR_CODES = frozenset(
    {
        "rate_limited",
        "transport_error",
        "permission_denied",
        "format_error",
        "unknown_feishu_error",
    }
)

_PROFILE_LABELS = {
    "conservative": "保守",
    "default": "默认",
    "aggressive": "激进",
}
_STATUS_LABELS = {
    "verified": "已验证预警",
    "early_signal": "早期信号·待核实",
}


class DeliverySendError(RuntimeError):
    def __init__(self, code: str) -> None:
        safe_code = code if code in _SAFE_FEISHU_ERROR_CODES else "unknown_feishu_error"
        super().__init__(safe_code)
        self.code = safe_code


def safe_feishu_error_code(error: object | None) -> str:
    if getattr(error, "raw_code", None) == 99992402:
        return "format_error"
    code = getattr(error, "code", None)
    value = getattr(code, "value", code)
    if not isinstance(value, str):
        return "unknown_feishu_error"
    normalized = value.casefold()
    if "rate" in normalized:
        return "rate_limited"
    if normalized in {"permission_denied", "target_revoked", "forbidden"}:
        return "permission_denied"
    if "format" in normalized:
        return "format_error"
    if normalized in {
        "network",
        "network_error",
        "transport",
        "transport_error",
        "timeout",
        "send_timeout",
        "not_connected",
    }:
        return "transport_error"
    return "unknown_feishu_error"


class FeishuDeliveryPort:
    def __init__(self, channel: Any, renderer: Any) -> None:
        self._channel = channel
        self._renderer = renderer

    async def send(self, claim: DeliveryClaim) -> str:
        options = {
            "reply_to": claim.reply_to_message_id,
            "reply_in_thread": claim.reply_in_thread,
            "uuid": claim.idempotency_key,
        }
        try:
            message = self._renderer.render(claim)
        except Exception:
            raise DeliverySendError("format_error") from None
        try:
            result = await self._channel.send(claim.group_id, message, options)
            if not getattr(result, "success", False):
                code = safe_feishu_error_code(getattr(result, "error", None))
                if code != "format_error" or "card" not in message:
                    raise DeliverySendError(code)
                fallback_options = {
                    **options,
                    "uuid": hashlib.sha256(
                        f"{claim.idempotency_key}:text".encode()
                    ).hexdigest()[:32],
                }
                result = await self._channel.send(
                    claim.group_id,
                    {"text": semantic_to_text(claim.payload)},
                    fallback_options,
                )
                if not getattr(result, "success", False):
                    raise DeliverySendError(
                        safe_feishu_error_code(getattr(result, "error", None))
                    )
            message_id = getattr(result, "message_id", None)
            if not isinstance(message_id, str) or not message_id:
                raise DeliverySendError("unknown_feishu_error")
        except asyncio.CancelledError:
            raise
        except DeliverySendError:
            raise
        except Exception as error:
            raise DeliverySendError(safe_feishu_error_code(error)) from None
        return message_id


class DeliveryRepository(Protocol):
    async def claim_delivery(self, *, now: datetime) -> DeliveryClaim | None: ...

    async def claim_delivery_by_id(
        self, outbox_id: int, *, now: datetime
    ) -> DeliveryClaim | None: ...

    async def mark_delivery_sent(
        self, claim: DeliveryClaim, *, message_id: str, now: datetime
    ) -> None: ...

    async def fail_delivery(self, claim: DeliveryClaim, code: str, *, now: datetime) -> None: ...

    async def skip_delivery(self, claim: DeliveryClaim, code: str) -> None: ...


class DeliveryPort(Protocol):
    async def send(self, claim: DeliveryClaim) -> str: ...


class ActiveBindingStore(Protocol):
    async def is_active(self, group_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class DeliverySummary:
    sent: int
    failed: int
    skipped: int


def _plain(value: object, *, limit: int | None = None) -> str:
    sanitized = "".join(
        " " if character.isspace() else "" if category(character) == "Cc" else character
        for character in str(value)
    )
    text = " ".join(sanitized.split())
    if limit is not None and len(text) > limit:
        text = f"{text[: max(limit - 1, 0)]}…"
    for token in ("\\", "`", "*", "_", "[", "]", "<", ">"):
        text = text.replace(token, f"\\{token}")
    return text


def _safe_source_reference(name: object, url: object) -> str:
    label = _plain(name) or "来源"
    raw_url = str(url).strip()
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        parsed = None
    unsafe_netloc = parsed is not None and any(
        character.isspace() or character in "()[]<>\\" for character in parsed.netloc
    )
    if (
        parsed is None
        or parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or unsafe_netloc
    ):
        return f"原文：{label}（链接不可用）"
    safe_url = urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc,
            quote(parsed.path, safe="/%:@"),
            quote(parsed.query, safe="=&%:@/?"),
            quote(parsed.fragment, safe="%:@/?"),
        )
    )
    return f"原文：[{label}]({safe_url})"


def alert_markdown(item: dict[str, object], *, compact: bool = False) -> str:
    field_limit = 50 if compact else None
    rationale = "；".join(
        f"{_plain(row['claim'], limit=field_limit)}"
        f"（原文：{_plain(row['quote'], limit=field_limit)}）"
        for row in item["rationale"]
    )
    actions = "；".join(
        f"{_plain(row['action'], limit=field_limit)}"
        f"｜负责人：{_plain(row['owner_type'], limit=field_limit)}"
        f"｜期限：{_plain(row.get('deadline') or '未明确', limit=field_limit)}"
        for row in item["actions"]
    )
    uncertainties = (
        "；".join(_plain(value, limit=field_limit) for value in item["uncertainties"]) or "无"
    )
    profile = _PROFILE_LABELS.get(str(item["risk_profile"]), "未知")
    status = _STATUS_LABELS.get(str(item["verification_status"]), "待核实")
    headline_limit = 50 if compact else None
    value_limit = 50 if compact else None
    return (
        f"**{_plain(item['headline'], limit=headline_limit)}**\n"
        f"策略：{profile}｜状态：{status}\n"
        f"风险：{_plain(item['risk_level'])}"
        f"｜证据可信度：{_plain(item['evidence_confidence'])}\n"
        f"摘要：{_plain(item['summary'], limit=value_limit)}\n"
        f"影响：{_plain(item['impact'], limit=value_limit)}\n"
        f"判断依据：{rationale}\n"
        f"建议动作：{actions}\n"
        f"不确定性：{uncertainties}\n"
        f"{_safe_source_reference(item['source_name'], item['source_url'])}"
    )


def _message_theme(payload: dict[str, object], kind: MessageKind | None) -> str:
    if kind is MessageKind.DAILY_REPORT:
        return "blue"
    items = payload.get("items", [])
    if isinstance(items, list) and any(
        isinstance(item, dict) and item.get("verification_status") == "early_signal"
        for item in items
    ):
        return "orange"
    if kind is MessageKind.HIGH_ALERT:
        return "red"
    if kind is MessageKind.MEDIUM_ALERT_BATCH:
        return "orange"
    theme = payload.get("theme", "blue")
    return theme if theme in {"blue", "orange", "red"} else "blue"


def _title(payload: dict[str, object]) -> str:
    title = str(payload["title"])
    profile_label = _PROFILE_LABELS.get(str(payload.get("risk_profile", "")))
    if profile_label and f"策略：{profile_label}" not in title:
        title = f"{title} · 策略：{profile_label}"
    return title


def _markdown_block(content: str) -> dict[str, object]:
    return {
        "tag": "div",
        "text": {"tag": "lark_md", "content": content},
    }


def semantic_to_card(
    payload: dict[str, object], *, kind: MessageKind | None = None
) -> dict[str, object]:
    sections = payload.get("sections")
    items = payload.get("items", [])
    if isinstance(items, list) and items:
        blocks = [_markdown_block(alert_markdown(item)) for item in items]
    elif isinstance(sections, list):
        blocks = [
            _markdown_block(
                f"**{_plain(section['title'])}**\n"
                + "\n".join(f"- {_plain(item)}" for item in section["items"])
            )
            for section in sections
        ]
    else:
        blocks = [_markdown_block(alert_markdown(item)) for item in items]
    return {
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": _message_theme(payload, kind),
                "title": {"tag": "plain_text", "content": _plain(_title(payload))},
            },
            "elements": blocks,
        }
    }


def semantic_to_text(payload: dict[str, object]) -> str:
    lines = [_plain(_title(payload), limit=300)]
    used_bytes = len(lines[0].encode("utf-8"))

    def append_complete(value: str) -> bool:
        nonlocal used_bytes
        encoded = f"\n{value}".encode()
        if used_bytes + len(encoded) > TEXT_UTF8_LIMIT:
            return False
        lines.append(value)
        used_bytes += len(encoded)
        return True

    items = payload.get("items", [])
    sections = payload.get("sections")
    if isinstance(items, list) and items:
        for item in items[:MAX_FALLBACK_ITEMS]:
            if not append_complete(alert_markdown(item, compact=True)):
                break
    elif isinstance(sections, list):
        remaining = MAX_FALLBACK_ITEMS
        for section in sections:
            if remaining == 0:
                break
            if not append_complete(f"\n{_plain(section['title'])}"):
                break
            items = list(section["items"])[:remaining]
            for item in items:
                if not append_complete(f"- {_plain(item)}"):
                    return "\n".join(lines)
                remaining -= 1
    else:
        for item in items:
            if not append_complete(alert_markdown(item, compact=True)):
                break
    return "\n".join(lines)


class FeishuMessageRenderer:
    def render(self, claim: DeliveryClaim) -> dict[str, object]:
        if claim.kind is MessageKind.QA_ANSWER:
            if set(claim.payload) != {"text"}:
                raise DeliverySendError("format_error")
            text = claim.payload["text"]
            if not isinstance(text, str):
                raise DeliverySendError("format_error")
            try:
                encoded = text.encode("utf-8")
            except UnicodeEncodeError:
                raise DeliverySendError("format_error") from None
            if len(encoded) > TEXT_UTF8_LIMIT:
                raise DeliverySendError("format_error")
            return {"text": text}
        card = semantic_to_card(claim.payload, kind=claim.kind)
        encoded = json.dumps(card, ensure_ascii=False).encode("utf-8")
        if len(encoded) <= CARD_UTF8_LIMIT:
            return card
        return {"text": semantic_to_text(claim.payload)}


class DeliveryWorker:
    def __init__(
        self,
        repository: DeliveryRepository,
        port: DeliveryPort,
        *,
        bindings: ActiveBindingStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._port = port
        self._bindings = bindings
        self._clock = clock

    async def drain(self, *, limit: int) -> DeliverySummary:
        sent = failed = skipped = 0
        for _ in range(limit):
            claim = await self._repository.claim_delivery(now=self._clock())
            if claim is None:
                break
            outcome = await self._deliver(claim)
            sent += int(outcome == "sent")
            failed += int(outcome == "failed")
            skipped += int(outcome == "skipped")
        return DeliverySummary(sent=sent, failed=failed, skipped=skipped)

    async def send_id(self, outbox_id: int) -> DeliverySummary:
        claim = await self._repository.claim_delivery_by_id(outbox_id, now=self._clock())
        if claim is None:
            return DeliverySummary(sent=0, failed=0, skipped=0)
        outcome = await self._deliver(claim)
        return DeliverySummary(
            sent=int(outcome == "sent"),
            failed=int(outcome == "failed"),
            skipped=int(outcome == "skipped"),
        )

    async def _deliver(self, claim: DeliveryClaim) -> str:
        if not claim.group_id or (
            self._bindings is not None and not await self._bindings.is_active(claim.group_id)
        ):
            await self._repository.skip_delivery(claim, "no_active_binding")
            return "skipped"
        try:
            message_id = await self._port.send(claim)
        except DeliverySendError as error:
            await self._repository.fail_delivery(claim, error.code, now=self._clock())
            return "failed"
        await self._repository.mark_delivery_sent(claim, message_id=message_id, now=self._clock())
        return "sent"


__all__ = [
    "DeliverySendError",
    "DeliverySummary",
    "DeliveryWorker",
    "FeishuDeliveryPort",
    "FeishuMessageRenderer",
    "alert_markdown",
    "safe_feishu_error_code",
    "semantic_to_card",
    "semantic_to_text",
]
