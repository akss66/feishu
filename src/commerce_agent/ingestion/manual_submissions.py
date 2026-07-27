from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from commerce_agent.domain import InboundMessage
from commerce_agent.ingestion.models import Platform, SourceDefinition
from commerce_agent.ingestion.official_notices import (
    OfficialAccountRegistry,
    OfficialNotice,
    validate_notice,
)
from commerce_agent.persistence.ingestion import PersistableDocument


class ManualSubmissionRepository(Protocol):
    async def sync_sources(self, sources: tuple[SourceDefinition, ...]) -> None: ...

    async def persist_version(self, candidate: PersistableDocument) -> Any: ...

    async def record_official_notice_audit(self, **values: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class ParsedManualSubmission:
    platform: Platform
    source_account: str
    title: str
    original_url: str
    body: str


@dataclass(frozen=True, slots=True)
class ManualSubmissionResult:
    audit_id: str
    document_id: int
    version_id: int


def parse_manual_submission(text: str) -> ParsedManualSubmission:
    normalized = text.replace("\r\n", "\n").strip()
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "提交情报":
        raise ValueError("invalid_manual_submission")
    try:
        body_index = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "正文:"
        )
    except StopIteration:
        raise ValueError("invalid_manual_submission") from None

    headers: dict[str, str] = {}
    for line in lines[1:body_index]:
        key, separator, value = line.partition(":")
        if not separator:
            key, separator, value = line.partition("：")
        if separator:
            headers[key.strip()] = value.strip()
    body = "\n".join(lines[body_index + 1 :]).strip()
    required = ("平台", "来源账号", "标题", "原文")
    if any(not headers.get(field) for field in required) or not body:
        raise ValueError("invalid_manual_submission")
    try:
        platform = Platform(headers["平台"].lower())
    except ValueError:
        raise ValueError("invalid_manual_submission_platform") from None
    return ParsedManualSubmission(
        platform=platform,
        source_account=headers["来源账号"],
        title=headers["标题"],
        original_url=headers["原文"],
        body=body,
    )


class ManualSubmissionService:
    def __init__(
        self,
        accounts: OfficialAccountRegistry,
        repository: ManualSubmissionRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        audit_id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._accounts = accounts
        self._repository = repository
        self._clock = clock
        self._audit_id_factory = audit_id_factory

    async def submit(self, message: InboundMessage) -> ManualSubmissionResult:
        parsed = parse_manual_submission(message.text)
        received_at = self._clock()
        submitted_by = message.sender_id or message.chat_id
        notice = OfficialNotice(
            platform=parsed.platform,
            source_account=parsed.source_account,
            original_url=parsed.original_url,
            title=parsed.title,
            body=parsed.body,
            published_at=None,
            received_at=received_at,
            submitted_by=submitted_by,
            transport="feishu",
        )
        return await self.submit_notice(notice)

    async def submit_notice(self, notice: OfficialNotice) -> ManualSubmissionResult:
        account = validate_notice(notice, self._accounts)
        await self._repository.sync_sources((account.as_source_definition(),))

        normalized_body = "\n".join(
            line.rstrip() for line in notice.body.strip().splitlines()
        )
        content_hash = _sha256(normalized_body)
        content_group_hash = _sha256(f"{notice.title.strip()}\n{normalized_body}")
        outcome = await self._repository.persist_version(
            PersistableDocument(
                source_id=account.source_id,
                canonical_url=notice.original_url,
                title=notice.title.strip(),
                body=normalized_body,
                language="zh",
                language_confidence=1.0,
                content_hash=content_hash,
                content_group_hash=content_group_hash,
                fetched_at=notice.received_at,
                author=account.display_name,
                published_at=notice.published_at,
                publisher_key=account.publisher_key,
                attribution=account.display_name,
                content_scope="full_text",
            )
        )
        audit_id = self._audit_id_factory()
        await self._repository.record_official_notice_audit(
            audit_id=audit_id,
            transport=notice.transport,
            source_account=account.account_id,
            platform=notice.platform.value,
            submitted_by_hash=_sha256(notice.submitted_by),
            original_url_hash=_sha256(notice.original_url),
            status="accepted",
            error_code=None,
            received_at=notice.received_at,
        )
        return ManualSubmissionResult(
            audit_id=audit_id,
            document_id=outcome.document_id,
            version_id=outcome.version_id,
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
