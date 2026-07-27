from datetime import UTC, datetime

import pytest

from commerce_agent.domain import InboundMessage
from commerce_agent.ingestion.manual_submissions import (
    ManualSubmissionService,
    parse_manual_submission,
)
from commerce_agent.ingestion.models import Platform
from commerce_agent.ingestion.official_notices import OfficialAccountRegistry

SAMPLE = """提交情报
平台: amazon
来源账号: 亚马逊全球开店
标题: 亚马逊更新某项卖家政策
原文: https://mp.weixin.qq.com/s/example
正文:
这里是团队成员从官方渠道取得并允许内部分析的完整正文。
"""


def test_parse_manual_submission() -> None:
    parsed = parse_manual_submission(SAMPLE)

    assert parsed.platform is Platform.AMAZON
    assert parsed.original_url == "https://mp.weixin.qq.com/s/example"
    assert parsed.body.startswith("这里是")


@pytest.mark.parametrize("field", ["平台", "来源账号", "标题", "原文", "正文"])
def test_parse_requires_every_field(field: str) -> None:
    text = SAMPLE.replace(f"{field}:", f"缺少{field}:")

    with pytest.raises(ValueError, match="invalid_manual_submission"):
        parse_manual_submission(text)


class FakeRepository:
    def __init__(self) -> None:
        self.sources = ()
        self.candidate = None
        self.audit = None

    async def sync_sources(self, sources) -> None:
        self.sources = tuple(sources)

    async def persist_version(self, candidate):
        self.candidate = candidate
        return type(
            "Outcome",
            (),
            {
                "document_id": 1,
                "version_id": 2,
                "created_document": True,
                "created_version": True,
            },
        )()

    async def record_official_notice_audit(self, **values) -> None:
        self.audit = values


async def test_submit_persists_full_text_and_hash_only_audit(tmp_path) -> None:
    accounts = OfficialAccountRegistry.from_yaml(
        tmp_path / "official_accounts.yaml",
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
    repository = FakeRepository()
    service = ManualSubmissionService(
        accounts,
        repository,
        clock=lambda: datetime(2026, 7, 27, 1, tzinfo=UTC),
        audit_id_factory=lambda: "audit123",
    )
    message = InboundMessage(
        chat_id="chat-one",
        message_id="msg-one",
        text=SAMPLE,
        sender_id="user-123",
    )

    result = await service.submit(message)

    assert result.audit_id == "audit123"
    assert repository.sources[0].source_id == "official-notice-amazon-global-selling-cn"
    assert repository.candidate.content_scope == "full_text"
    assert repository.candidate.body.startswith("这里是")
    assert repository.audit["status"] == "accepted"
    assert repository.audit["submitted_by_hash"] != "user-123"
    assert repository.audit["original_url_hash"] != "https://mp.weixin.qq.com/s/example"
    assert "user-123" not in repository.audit.values()
    assert "https://mp.weixin.qq.com/s/example" not in repository.audit.values()
