from datetime import UTC, datetime
from pathlib import Path

from commerce_agent.ingestion.email_notices import (
    EmailAttachment,
    ImapEnvelope,
    ImapOfficialNoticeProvider,
)
from commerce_agent.ingestion.models import Platform
from commerce_agent.ingestion.official_notices import OfficialAccountRegistry


class FakeImap:
    def __init__(self, messages: tuple[ImapEnvelope, ...]) -> None:
        self.messages = messages
        self.processed: list[str] = []

    def fetch_unseen(self) -> tuple[ImapEnvelope, ...]:
        return self.messages

    def mark_processed(self, uid: str) -> None:
        self.processed.append(uid)


def _accounts(tmp_path: Path) -> OfficialAccountRegistry:
    return OfficialAccountRegistry.from_yaml(
        tmp_path / "accounts.yaml",
        document={
            "version": 1,
            "accounts": [
                {
                    "account_id": "amazon-global-selling-cn",
                    "source_id": "official-notice-amazon-global-selling-cn",
                    "display_name": "亚马逊全球开店",
                    "platforms": ["amazon"],
                    "publisher_key": "amazon.com",
                    "allowed_hosts": ["mp.weixin.qq.com"],
                    "transports": ["feishu", "email"],
                }
            ],
        },
    )


def _message(
    *,
    sender: str = "notice@amazon.com",
    attachments: tuple[EmailAttachment, ...] = (),
) -> ImapEnvelope:
    return ImapEnvelope(
        uid="101",
        sender=sender,
        subject="亚马逊更新某项卖家政策",
        body_text=(
            "官方公告原文：https://mp.weixin.qq.com/s/example\n"
            "这里是允许内部分析的完整正文。"
        ),
        received_at=datetime(2026, 7, 27, 1, tzinfo=UTC),
        raw_size=500,
        attachments=attachments,
    )


async def test_poll_accepts_only_allowlisted_sender(tmp_path) -> None:
    fake = FakeImap((_message(), _message(sender="attacker@example.com")))
    provider = ImapOfficialNoticeProvider(
        fake,
        accounts=_accounts(tmp_path),
        allowed_senders={"notice@amazon.com": "amazon-global-selling-cn"},
        max_message_bytes=1_000_000,
        max_attachment_bytes=2_000_000,
    )

    notices = await provider.poll()

    assert [notice.platform for notice in notices] == [Platform.AMAZON]
    assert fake.processed == ["101"]


async def test_oversized_attachment_is_rejected(tmp_path) -> None:
    attachment = EmailAttachment("notice.txt", "text/plain", b"x" * 2_000_001)
    fake = FakeImap((_message(attachments=(attachment,)),))
    provider = ImapOfficialNoticeProvider(
        fake,
        accounts=_accounts(tmp_path),
        allowed_senders={"notice@amazon.com": "amazon-global-selling-cn"},
        max_message_bytes=1_000_000,
        max_attachment_bytes=2_000_000,
    )

    assert await provider.poll() == ()
    assert provider.last_error_code == "attachment_too_large"
    assert fake.processed == []


async def test_unsupported_attachment_is_rejected(tmp_path) -> None:
    attachment = EmailAttachment("notice.exe", "application/octet-stream", b"safe")
    provider = ImapOfficialNoticeProvider(
        FakeImap((_message(attachments=(attachment,)),)),
        accounts=_accounts(tmp_path),
        allowed_senders={"notice@amazon.com": "amazon-global-selling-cn"},
    )

    assert await provider.poll() == ()
    assert provider.last_error_code == "unsupported_attachment"
