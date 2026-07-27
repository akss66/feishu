from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from commerce_agent.ingestion.models import ContentScope, Platform
from commerce_agent.ingestion.official_notices import (
    NoticeValidationError,
    OfficialAccountRegistry,
    OfficialNotice,
    validate_notice,
)

OFFICIAL_ACCOUNTS = (
    Path(__file__).parents[2]
    / "src"
    / "commerce_agent"
    / "sources"
    / "official_accounts.yaml"
)


def _notice() -> OfficialNotice:
    return OfficialNotice(
        platform=Platform.AMAZON,
        source_account="亚马逊全球开店",
        original_url="https://mp.weixin.qq.com/s/example",
        title="亚马逊更新某项卖家政策",
        body="这里是团队成员从官方渠道取得并允许内部分析的完整正文。",
        published_at=None,
        received_at=datetime(2026, 7, 27, 1, tzinfo=UTC),
        submitted_by="user-123",
        transport="feishu",
    )


def test_registry_builds_full_text_manual_source() -> None:
    registry = OfficialAccountRegistry.from_yaml(OFFICIAL_ACCOUNTS)
    account = registry.require("亚马逊全球开店")

    source = account.as_source_definition()

    assert source.source_id == "official-notice-amazon-global-selling-cn"
    assert source.platforms == (Platform.AMAZON,)
    assert source.content_scope is ContentScope.FULL_TEXT
    assert source.publisher_key == "amazon.com"


def test_validate_exact_official_account_and_link() -> None:
    registry = OfficialAccountRegistry.from_yaml(OFFICIAL_ACCOUNTS)

    account = validate_notice(_notice(), registry)

    assert account.display_name == "亚马逊全球开店"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/not-wechat",
        "http://mp.weixin.qq.com/s/insecure",
        "https://mp.weixin.qq.com.example/s/fake",
    ],
)
def test_rejects_untrusted_official_link(url: str) -> None:
    registry = OfficialAccountRegistry.from_yaml(OFFICIAL_ACCOUNTS)

    with pytest.raises(NoticeValidationError, match="untrusted_original_url"):
        validate_notice(replace(_notice(), original_url=url), registry)


@pytest.mark.parametrize(
    "body",
    [
        "订单号: ABCDEF123456，政策详情如下。",
        "买家邮箱 buyer@example.com，政策详情如下。",
        "余额: 10 元，政策详情如下。",
        "buyer phone +1 202 555 0199",
    ],
)
def test_rejects_account_level_private_data(body: str) -> None:
    registry = OfficialAccountRegistry.from_yaml(OFFICIAL_ACCOUNTS)

    with pytest.raises(NoticeValidationError, match="account_private_data"):
        validate_notice(replace(_notice(), body=body), registry)


def test_rejects_account_name_impersonation() -> None:
    registry = OfficialAccountRegistry.from_yaml(OFFICIAL_ACCOUNTS)

    with pytest.raises(NoticeValidationError, match="unknown_official_account"):
        validate_notice(replace(_notice(), source_account="亚马逊全球开店官方"), registry)
