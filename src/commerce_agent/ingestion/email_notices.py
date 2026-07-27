from __future__ import annotations

import asyncio
import email
import imaplib
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Protocol

from commerce_agent.ingestion.models import Trigger
from commerce_agent.ingestion.official_notices import (
    NoticeValidationError,
    OfficialAccountRegistry,
    OfficialNotice,
    validate_notice,
)

_URL_PATTERN = re.compile(r"https://[^\s<>\"']+")
_ALLOWED_ATTACHMENTS = {".txt", ".html", ".htm"}


@dataclass(frozen=True, slots=True)
class EmailAttachment:
    filename: str
    content_type: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class ImapEnvelope:
    uid: str
    sender: str
    subject: str
    body_text: str
    received_at: datetime
    raw_size: int
    attachments: tuple[EmailAttachment, ...] = ()


class ImapPort(Protocol):
    def fetch_unseen(self) -> tuple[ImapEnvelope, ...]: ...

    def mark_processed(self, uid: str) -> None: ...


class OfficialNoticeSink(Protocol):
    async def submit_notice(self, notice: OfficialNotice) -> object: ...


class EmailNoticeIngestionService:
    def __init__(
        self,
        provider: ImapOfficialNoticeProvider,
        sink: OfficialNoticeSink,
    ) -> None:
        self._provider = provider
        self._sink = sink

    async def run_all(self, trigger: Trigger) -> tuple[object, ...]:
        del trigger
        notices = await self._provider.poll()
        return tuple([await self._sink.submit_notice(notice) for notice in notices])


class ImapOfficialNoticeProvider:
    def __init__(
        self,
        client: ImapPort,
        *,
        accounts: OfficialAccountRegistry,
        allowed_senders: dict[str, str],
        max_message_bytes: int = 1_000_000,
        max_attachment_bytes: int = 2_000_000,
    ) -> None:
        self._client = client
        self._accounts = accounts
        self._allowed_senders = {
            sender.strip().lower(): account_id
            for sender, account_id in allowed_senders.items()
        }
        self._max_message_bytes = max_message_bytes
        self._max_attachment_bytes = max_attachment_bytes
        self.last_error_code: str | None = None
        self._processed_uid_hashes: set[str] = set()

    async def poll(self) -> tuple[OfficialNotice, ...]:
        return await asyncio.to_thread(self._poll_sync)

    def _poll_sync(self) -> tuple[OfficialNotice, ...]:
        import hashlib

        self.last_error_code = None
        accepted: list[OfficialNotice] = []
        for message in self._client.fetch_unseen():
            uid_hash = hashlib.sha256(message.uid.encode("utf-8")).hexdigest()
            if uid_hash in self._processed_uid_hashes:
                continue
            account_id = self._allowed_senders.get(message.sender.strip().lower())
            if account_id is None:
                continue
            if message.raw_size > self._max_message_bytes:
                self.last_error_code = "message_too_large"
                continue
            attachment_text: list[str] = []
            rejected = False
            for attachment in message.attachments:
                if len(attachment.payload) > self._max_attachment_bytes:
                    self.last_error_code = "attachment_too_large"
                    rejected = True
                    break
                suffix = _suffix(attachment.filename)
                if suffix == ".pdf" or suffix not in _ALLOWED_ATTACHMENTS:
                    self.last_error_code = "unsupported_attachment"
                    rejected = True
                    break
                decoded = attachment.payload.decode("utf-8", errors="replace")
                attachment_text.append(
                    _html_to_text(decoded) if suffix in {".html", ".htm"} else decoded
                )
            if rejected:
                continue
            try:
                account = self._accounts.require_id(account_id)
                body = "\n\n".join(
                    part.strip()
                    for part in (message.body_text, *attachment_text)
                    if part.strip()
                )
                match = _URL_PATTERN.search(body)
                if match is None:
                    raise NoticeValidationError("missing_original_url")
                notice = OfficialNotice(
                    platform=account.platforms[0],
                    source_account=account.display_name,
                    original_url=match.group(0).rstrip("。.,，)）"),
                    title=message.subject.strip(),
                    body=body,
                    published_at=None,
                    received_at=message.received_at,
                    submitted_by=message.sender.strip().lower(),
                    transport="email",
                )
                validate_notice(notice, self._accounts)
            except NoticeValidationError as error:
                self.last_error_code = error.code
                continue
            accepted.append(notice)
            self._client.mark_processed(message.uid)
            self._processed_uid_hashes.add(uid_hash)
        return tuple(accepted)


class StdlibImapClient:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        folder: str = "INBOX",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._folder = folder

    def fetch_unseen(self) -> tuple[ImapEnvelope, ...]:
        with imaplib.IMAP4_SSL(
            self._host,
            self._port,
            ssl_context=ssl.create_default_context(),
        ) as client:
            client.login(self._username, self._password)
            status, _ = client.select(self._folder, readonly=False)
            if status != "OK":
                raise RuntimeError("imap_select_failed")
            status, data = client.uid("search", None, "UNSEEN")
            if status != "OK":
                raise RuntimeError("imap_search_failed")
            envelopes: list[ImapEnvelope] = []
            for uid_bytes in data[0].split():
                uid = uid_bytes.decode("ascii")
                status, payload = client.uid("fetch", uid, "(RFC822)")
                if status != "OK" or not payload or not isinstance(payload[0], tuple):
                    continue
                raw = bytes(payload[0][1])
                envelopes.append(_parse_message(uid, raw))
            return tuple(envelopes)

    def mark_processed(self, uid: str) -> None:
        with imaplib.IMAP4_SSL(
            self._host,
            self._port,
            ssl_context=ssl.create_default_context(),
        ) as client:
            client.login(self._username, self._password)
            status, _ = client.select(self._folder, readonly=False)
            if status != "OK":
                raise RuntimeError("imap_select_failed")
            client.uid("store", uid, "+FLAGS", r"(\Seen)")


def parse_allowed_senders(value: str) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for item in value.split(","):
        sender, separator, account_id = item.strip().partition("=")
        normalized = sender.strip().lower()
        if not separator or "@" not in normalized or not account_id.strip():
            raise ValueError("invalid_official_notice_email_allowed_senders")
        mappings[normalized] = account_id.strip()
    if not mappings:
        raise ValueError("invalid_official_notice_email_allowed_senders")
    return mappings


def _parse_message(uid: str, raw: bytes) -> ImapEnvelope:
    message = email.message_from_bytes(raw)
    sender = parseaddr(message.get("From", ""))[1].lower()
    subject = str(make_header(decode_header(message.get("Subject", ""))))
    body_parts: list[str] = []
    attachments: list[EmailAttachment] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            attachments.append(
                EmailAttachment(
                    filename=str(make_header(decode_header(filename))),
                    content_type=part.get_content_type(),
                    payload=payload,
                )
            )
            continue
        charset = part.get_content_charset() or "utf-8"
        decoded = payload.decode(charset, errors="replace")
        body_parts.append(
            _html_to_text(decoded)
            if part.get_content_type() == "text/html"
            else decoded
        )
    received_at = _message_datetime(message)
    return ImapEnvelope(
        uid=uid,
        sender=sender,
        subject=subject,
        body_text="\n".join(body_parts).strip(),
        received_at=received_at,
        raw_size=len(raw),
        attachments=tuple(attachments),
    )


def _message_datetime(message: Message) -> datetime:
    raw_date = message.get("Date")
    if raw_date:
        try:
            parsed = parsedate_to_datetime(raw_date)
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC)
        except (TypeError, ValueError):
            pass
    return datetime.now(UTC)


def _suffix(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return "\n".join(parser.parts)
