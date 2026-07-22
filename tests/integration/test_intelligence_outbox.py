from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from commerce_agent.ingestion.models import (
    CollectorKind,
    ComplianceStatus,
    Platform,
    SourceDefinition,
    TrustTier,
)
from commerce_agent.intelligence.models import (
    ActionItem,
    AnalysisCandidate,
    AnalysisResult,
    DeliveryMessage,
    EventType,
    EvidenceClaim,
    MessageKind,
    RiskLevel,
    RiskProfile,
    RiskResolution,
    ScoredAnalysis,
)
from commerce_agent.intelligence.reports import AlertComposer, DailyReportComposer
from commerce_agent.intelligence.repository import (
    SqlAlchemyIntelligenceRepository,
    StaleLeaseError,
)
from commerce_agent.intelligence.risk import RiskPolicy
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.ingestion import (
    PersistableDocument,
    SqlAlchemyIngestionRepository,
)
from commerce_agent.persistence.intelligence_preferences import (
    SqlAlchemyIntelligencePreferenceStore,
)
from commerce_agent.persistence.models import DailyReport, DeliveryOutbox

NOW = datetime(2026, 7, 21, 1, tzinfo=UTC)


def _source() -> SourceDefinition:
    return SourceDefinition(
        source_id="source-one",
        name="Seller News",
        entry_url="https://example.com/news",
        platforms=(Platform.EBAY,),
        trust_tier=TrustTier.OFFICIAL,
        collector=CollectorKind.RSS,
        compliance=ComplianceStatus.ALLOWED,
        enabled=True,
        regions=("global",),
        language_hint="en",
        interval_minutes=120,
        terms_url="https://example.com/terms",
        robots_url="https://example.com/robots.txt",
        reviewed_at=date(2026, 7, 20),
        compliance_notes="Public feed approved for collection.",
        collector_config={"item_limit": 50},
    )


def _result(risk: RiskLevel = RiskLevel.MEDIUM) -> AnalysisResult:
    return AnalysisResult(
        headline_zh="eBay 全球政策更新",
        summary_zh=(
            "eBay 发布新的政策更新，卖家需要核对适用站点、商品类别、生效日期与账户范围，"
            "重新评估对定价、库存和运营流程的影响，并在采取业务动作前核实官方原文，"
            "同时将结论同步给负责人持续跟进。"
        ),
        event_type=EventType.MARKET_UPDATE,
        platforms=(Platform.EBAY,),
        regions=("global",),
        affected_seller_types=("all",),
        effective_at=None,
        risk_level=risk,
        impact="可能影响卖家的定价与运营安排",
        rationale=(EvidenceClaim(claim="政策发生变化", quote="policy changed"),),
        action_items=(ActionItem(action="复核影响", owner_type="运营"),),
        uncertainties=(),
        tags=("政策",),
    )


def _analysis(
    analysis_id: int,
    *,
    risk: RiskLevel = RiskLevel.MEDIUM,
    score: int = 80,
    fingerprint: str = "same-event",
    version_id: int | None = None,
    content_hash: str | None = None,
) -> ScoredAnalysis:
    candidate = AnalysisCandidate(
        job_id=analysis_id,
        lease_token=None,
        document_version_id=version_id or analysis_id,
        source_id="source-one",
        source_name="Seller News",
        trust_tier=TrustTier.OFFICIAL,
        canonical_url="https://example.com/news/policy-update",
        content_hash=content_hash or f"{analysis_id:064x}",
        title="Policy update",
        body="Policy update source body",
        language="en",
        language_confidence=0.99,
        author=None,
        published_at=None,
        fetched_at=NOW,
        platforms=(Platform.EBAY,),
        regions=("global",),
    )
    return ScoredAnalysis(
        analysis_id=analysis_id,
        candidate=candidate,
        result=_result(risk),
        evidence_confidence=score,
        resolution=RiskResolution(risk_level=risk, rule_hits=(), needs_review=False),
        event_fingerprint=fingerprint,
    )


async def _services(tmp_path, filename: str):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / filename}")
    await database.create_schema()
    repository = SqlAlchemyIntelligenceRepository(database.session)
    preferences = SqlAlchemyIntelligencePreferenceStore(database.session)
    composer = AlertComposer(repository, preferences, RiskPolicy())
    return database, repository, preferences, composer


async def _queue_raw(repository: SqlAlchemyIntelligenceRepository) -> int:
    ids = await repository.queue_alerts(
        (
            DeliveryMessage(
                idempotency_key="alert:chat-one:raw:medium:raw:0",
                group_id="chat-one",
                kind=MessageKind.MEDIUM_ALERT_BATCH,
                payload={"title": "fixed", "theme": "orange", "items": []},
            ),
        ),
        now=NOW,
        dedup_hours=24,
    )
    return ids[0]


def _requeue_key(base_key: str, terminal_id: int) -> str:
    digest = hashlib.sha256(base_key.encode("utf-8")).hexdigest()
    return f"alert-requeue:{digest}:r{terminal_id}"


async def _make_terminal(
    repository: SqlAlchemyIntelligenceRepository,
    outbox_id: int,
    status: str,
    *,
    now: datetime,
) -> datetime:
    if status == "skipped":
        claim = await repository.claim_delivery_by_id(outbox_id, now=now)
        assert claim is not None
        await repository.skip_delivery(claim, "no_active_binding")
        return now

    attempt_at = now
    for delay in (
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=30),
        timedelta(0),
    ):
        claim = await repository.claim_delivery_by_id(outbox_id, now=attempt_at)
        assert claim is not None
        await repository.fail_delivery(claim, "transport_error", now=attempt_at)
        attempt_at += delay
    return attempt_at


async def test_same_event_is_suppressed_for_rolling_24_hours_but_upgrade_is_allowed(
    tmp_path,
) -> None:
    database, _, _, composer = await _services(tmp_path, "dedup.db")
    try:
        medium = _analysis(1)
        first = await composer.queue_batch("chat-one", (medium,), now=NOW)
        duplicate = await composer.queue_batch(
            "chat-one", (medium,), now=NOW + timedelta(hours=23, minutes=59)
        )
        upgrade = await composer.queue_batch(
            "chat-one",
            (replace(medium, result=_result(RiskLevel.HIGH)),),
            now=NOW + timedelta(hours=23, minutes=59),
        )

        assert len(first) == 1
        assert duplicate == ()
        assert len(upgrade) == 1
    finally:
        await database.dispose()


async def test_same_event_can_be_queued_again_at_24_hour_boundary(tmp_path) -> None:
    database, _, _, composer = await _services(tmp_path, "dedup-boundary.db")
    try:
        item = _analysis(1)
        first = await composer.queue_batch("chat-one", (item,), now=NOW)
        next_window = await composer.queue_batch(
            "chat-one", (item,), now=NOW + timedelta(hours=24)
        )

        assert len(first) == 1
        assert len(next_window) == 1
    finally:
        await database.dispose()


async def test_alert_preview_shares_pending_and_sent_dedup_without_writing(
    tmp_path,
) -> None:
    database, repository, _, composer = await _services(tmp_path, "preview-active.db")
    try:
        item = _analysis(1)
        queued = await composer.queue_batch("chat-one", (item,), now=NOW)

        pending_preview = await composer.preview_batch(
            "chat-one", (item,), now=NOW + timedelta(hours=1)
        )
        claim = await repository.claim_delivery_by_id(
            queued[0], now=NOW + timedelta(hours=1)
        )
        assert claim is not None
        await repository.mark_delivery_sent(
            claim,
            message_id="message-one",
            now=NOW + timedelta(hours=1),
        )
        sent_preview = await composer.preview_batch(
            "chat-one", (item,), now=NOW + timedelta(hours=2)
        )

        assert pending_preview == ()
        assert sent_preview == ()
        assert len(await repository.list_outbox(queued)) == 1
    finally:
        await database.dispose()


async def test_alert_preview_allows_upgrade_or_changed_version_without_writing(
    tmp_path,
) -> None:
    database, repository, _, composer = await _services(tmp_path, "preview-upgrade.db")
    try:
        medium = _analysis(1, version_id=10, content_hash="a" * 64)
        queued = await composer.queue_batch("chat-one", (medium,), now=NOW)

        upgraded = await composer.preview_batch(
            "chat-one",
            (replace(medium, result=_result(RiskLevel.HIGH)),),
            now=NOW + timedelta(hours=1),
        )
        same_content = await composer.preview_batch(
            "chat-one",
            (_analysis(2, version_id=11, content_hash="a" * 64),),
            now=NOW + timedelta(hours=1),
        )
        changed_content = await composer.preview_batch(
            "chat-one",
            (_analysis(3, version_id=12, content_hash="b" * 64),),
            now=NOW + timedelta(hours=1),
        )

        assert len(upgraded) == 1
        assert same_content == ()
        assert len(changed_content) == 1
        assert len(await repository.list_outbox(queued)) == 1
    finally:
        await database.dispose()


@pytest.mark.parametrize("terminal_status", ["failed", "skipped"])
async def test_alert_preview_matches_queue_contract_for_terminal_rows(
    tmp_path,
    terminal_status: str,
) -> None:
    database, repository, _, composer = await _services(
        tmp_path, f"preview-{terminal_status}.db"
    )
    try:
        item = _analysis(1)
        queued = await composer.queue_batch("chat-one", (item,), now=NOW)
        terminal_at = await _make_terminal(
            repository,
            queued[0],
            terminal_status,
            now=NOW + timedelta(minutes=1),
        )

        preview = await composer.preview_batch(
            "chat-one", (item,), now=terminal_at + timedelta(minutes=1)
        )

        assert len(preview) == 1
        assert len(await repository.list_outbox(queued)) == 1
    finally:
        await database.dispose()


async def test_alert_deduplication_is_scoped_to_group(tmp_path) -> None:
    database, repository, _, _ = await _services(tmp_path, "group-scope.db")
    payload = {
        "title": "中风险预警汇总",
        "theme": "orange",
        "items": [
            {
                "event_fingerprint": "shared-event",
                "risk_level": "medium",
                "document_version_id": 1,
                "content_hash": "a" * 64,
            }
        ],
    }
    try:
        ids = await repository.queue_alerts(
            (
                DeliveryMessage(
                    idempotency_key="alert-batch:chat-one:shared:0",
                    group_id="chat-one",
                    kind=MessageKind.MEDIUM_ALERT_BATCH,
                    payload=payload,
                ),
                DeliveryMessage(
                    idempotency_key="alert-batch:chat-two:shared:0",
                    group_id="chat-two",
                    kind=MessageKind.MEDIUM_ALERT_BATCH,
                    payload=payload,
                ),
            ),
            now=NOW,
            dedup_hours=24,
        )

        assert len(ids) == 2
    finally:
        await database.dispose()


async def test_new_document_version_requires_changed_content_to_resend(tmp_path) -> None:
    database, _, _, composer = await _services(tmp_path, "new-version.db")
    try:
        first_item = _analysis(1, version_id=10, content_hash="a" * 64)
        same_content = _analysis(2, version_id=11, content_hash="a" * 64)
        changed_content = _analysis(3, version_id=12, content_hash="b" * 64)

        first = await composer.queue_batch("chat-one", (first_item,), now=NOW)
        suppressed = await composer.queue_batch(
            "chat-one", (same_content,), now=NOW + timedelta(hours=1)
        )
        resent = await composer.queue_batch(
            "chat-one", (changed_content,), now=NOW + timedelta(hours=2)
        )

        assert len(first) == 1
        assert suppressed == ()
        assert len(resent) == 1
    finally:
        await database.dispose()


async def test_partial_batch_deduplication_recomputes_verification_title(tmp_path) -> None:
    database, repository, preferences, composer = await _services(
        tmp_path, "partial-batch.db"
    )
    try:
        await preferences.set("chat-one", RiskProfile.AGGRESSIVE, now=NOW)
        early = _analysis(1, score=60, fingerprint="early")
        verified = _analysis(2, score=80, fingerprint="verified")
        await composer.queue_batch("chat-one", (early,), now=NOW)

        ids = await composer.queue_batch(
            "chat-one", (early, verified), now=NOW + timedelta(hours=1)
        )

        rows = await repository.list_outbox(ids)
        assert len(rows) == 1
        assert rows[0].payload["title"] == "中风险预警汇总"
        assert [item["event_fingerprint"] for item in rows[0].payload["items"]] == [
            "verified"
        ]
    finally:
        await database.dispose()


async def test_profile_switch_does_not_resend_recent_event(tmp_path) -> None:
    database, repository, preferences, composer = await _services(
        tmp_path, "profile-switch.db"
    )
    try:
        await preferences.set("chat-one", RiskProfile.AGGRESSIVE, now=NOW)
        item = _analysis(1, risk=RiskLevel.HIGH, score=90)
        first = await composer.queue_batch("chat-one", (item,), now=NOW)
        claim = await repository.claim_delivery_by_id(first[0], now=NOW)
        assert claim is not None
        await repository.mark_delivery_sent(claim, message_id="om_1", now=NOW)

        await preferences.set(
            "chat-one", RiskProfile.CONSERVATIVE, now=NOW + timedelta(hours=1)
        )

        assert await composer.queue_batch(
            "chat-one", (item,), now=NOW + timedelta(hours=1)
        ) == ()
    finally:
        await database.dispose()


async def test_completed_analysis_is_recovered_when_queueing_was_interrupted(
    tmp_path,
) -> None:
    database, repository, _, composer = await _services(tmp_path, "recovery.db")
    ingestion = SqlAlchemyIngestionRepository(database.session)
    try:
        await ingestion.sync_sources([_source()])
        await ingestion.persist_version(
            PersistableDocument(
                source_id="source-one",
                canonical_url="https://example.com/news/policy-update",
                title="Policy update",
                body="policy changed",
                language="en",
                language_confidence=0.99,
                content_hash="c" * 64,
                content_group_hash="group-c",
                fetched_at=NOW,
                author=None,
                published_at=NOW,
            )
        )
        claim = await repository.claim_next(now=NOW)
        assert claim is not None
        await repository.complete_analysis(
            claim,
            _result(RiskLevel.HIGH),
            90,
            "recovered-event",
            risk_level=RiskLevel.HIGH,
            now=NOW,
            model_name="test-model",
        )

        ids = await composer.queue_due("chat-one", now=NOW + timedelta(minutes=1))

        assert len(ids) == 1
        rows = await repository.list_outbox(ids)
        assert rows[0].payload["items"][0]["analysis_id"] == 1
    finally:
        await database.dispose()


async def test_two_workers_cannot_claim_the_same_delivery(tmp_path) -> None:
    database, repository, _, _ = await _services(tmp_path, "claim.db")
    competitor = SqlAlchemyIntelligenceRepository(database.session)
    try:
        await _queue_raw(repository)

        first, second = await asyncio.gather(
            repository.claim_delivery(now=NOW), competitor.claim_delivery(now=NOW)
        )

        claims = [claim for claim in (first, second) if claim is not None]
        assert len(claims) == 1
        assert claims[0].attempt_count == 1
    finally:
        await database.dispose()


async def test_stale_delivery_token_cannot_mutate_reclaimed_lease(tmp_path) -> None:
    database, repository, _, _ = await _services(tmp_path, "stale.db")
    try:
        outbox_id = await _queue_raw(repository)
        old = await repository.claim_delivery_by_id(outbox_id, now=NOW, lease_seconds=1)
        assert old is not None
        current = await repository.claim_delivery_by_id(
            outbox_id, now=NOW + timedelta(seconds=1)
        )
        assert current is not None

        with pytest.raises(StaleLeaseError):
            await repository.mark_delivery_sent(old, message_id="stale", now=NOW)
        with pytest.raises(StaleLeaseError):
            await repository.fail_delivery(old, "transport_error", now=NOW)

        await repository.mark_delivery_sent(
            current, message_id="current", now=NOW + timedelta(seconds=1)
        )
    finally:
        await database.dispose()


async def test_outbox_retry_schedule_is_one_five_thirty_then_failed(tmp_path) -> None:
    database, repository, _, _ = await _services(tmp_path, "retry.db")
    try:
        outbox_id = await _queue_raw(repository)
        expected_payload = {"title": "fixed", "theme": "orange", "items": []}
        attempt_at = NOW
        for delay in (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30)):
            claim = await repository.claim_delivery_by_id(outbox_id, now=attempt_at)
            assert claim is not None
            assert claim.payload == expected_payload
            await repository.fail_delivery(claim, "transport_error", now=attempt_at)
            attempt_at += delay
            assert await repository.next_delivery_time(outbox_id) == attempt_at

        final_claim = await repository.claim_delivery_by_id(outbox_id, now=attempt_at)
        assert final_claim is not None
        assert final_claim.attempt_count == 4
        await repository.fail_delivery(final_claim, "transport_error", now=attempt_at)

        row = (await repository.list_outbox((outbox_id,)))[0]
        assert row.status == "failed"
        assert row.attempt_count == 4
        assert row.next_attempt_at is None
        assert row.payload == expected_payload
    finally:
        await database.dispose()


@pytest.mark.parametrize("status", ["pending", "sending", "retry_wait", "sent"])
async def test_active_or_delivered_alert_still_suppresses_recent_event(
    tmp_path, status: str
) -> None:
    database, repository, _, composer = await _services(tmp_path, f"suppress-{status}.db")
    item = _analysis(1)
    try:
        (outbox_id,) = await composer.queue_batch("chat-one", (item,), now=NOW)
        if status != "pending":
            claim = await repository.claim_delivery_by_id(outbox_id, now=NOW)
            assert claim is not None
            if status == "retry_wait":
                await repository.fail_delivery(claim, "transport_error", now=NOW)
            elif status == "sent":
                await repository.mark_delivery_sent(
                    claim, message_id="om_delivered", now=NOW
                )

        row = (await repository.list_outbox((outbox_id,)))[0]
        assert row.status == status
        assert await composer.queue_batch(
            "chat-one", (item,), now=NOW + timedelta(hours=1)
        ) == ()
    finally:
        await database.dispose()


async def test_no_active_binding_skip_can_queue_same_event_again(tmp_path) -> None:
    database, repository, _, composer = await _services(tmp_path, "requeue-skip.db")
    item = _analysis(1)
    try:
        (old_id,) = await composer.queue_batch("chat-one", (item,), now=NOW)
        claim = await repository.claim_delivery_by_id(old_id, now=NOW)
        assert claim is not None
        old_key = claim.idempotency_key
        old_payload = claim.payload
        await repository.skip_delivery(claim, "no_active_binding")

        (new_id,) = await composer.queue_batch(
            "chat-one", (item,), now=NOW + timedelta(hours=1)
        )

        old, new = await repository.list_outbox((old_id, new_id))
        assert old.status == "skipped"
        assert old.safe_error_code == "no_active_binding"
        assert old.payload == old_payload
        assert old.idempotency_key == old_key
        assert new.status == "pending"
        assert new.payload == old_payload
        assert new.idempotency_key == _requeue_key(old_key, old_id)
    finally:
        await database.dispose()


async def test_terminal_failure_can_queue_same_event_again(tmp_path) -> None:
    database, repository, _, composer = await _services(tmp_path, "requeue-failed.db")
    item = _analysis(1)
    try:
        (old_id,) = await composer.queue_batch("chat-one", (item,), now=NOW)
        attempt_at = NOW
        for delay in (
            timedelta(minutes=1),
            timedelta(minutes=5),
            timedelta(minutes=30),
            timedelta(0),
        ):
            claim = await repository.claim_delivery_by_id(old_id, now=attempt_at)
            assert claim is not None
            old_key = claim.idempotency_key
            old_payload = claim.payload
            await repository.fail_delivery(claim, "transport_error", now=attempt_at)
            attempt_at += delay

        (new_id,) = await composer.queue_batch(
            "chat-one", (item,), now=attempt_at + timedelta(hours=1)
        )

        old, new = await repository.list_outbox((old_id, new_id))
        assert old.status == "failed"
        assert old.attempt_count == 4
        assert old.safe_error_code == "transport_error"
        assert old.payload == old_payload
        assert old.idempotency_key == old_key
        assert new.status == "pending"
        assert new.attempt_count == 0
        assert new.payload == old_payload
        assert new.idempotency_key == _requeue_key(old_key, old_id)
    finally:
        await database.dispose()


async def test_overlong_base_key_supports_multiple_terminal_requeues(tmp_path) -> None:
    database, repository, _, _ = await _services(tmp_path, "long-key-requeues.db")
    base_key = f"alert:{'long-segment-' * 30}"
    payload = {
        "title": "中风险预警汇总",
        "theme": "orange",
        "items": [
            {
                "event_fingerprint": "long-key-event",
                "risk_level": "medium",
                "document_version_id": 1,
                "content_hash": "a" * 64,
            }
        ],
    }
    message = DeliveryMessage(
        idempotency_key=base_key,
        group_id="chat-one",
        kind=MessageKind.MEDIUM_ALERT_BATCH,
        payload=payload,
    )
    try:
        (first_id,) = await repository.queue_alerts(
            (message,), now=NOW, dedup_hours=24
        )
        await _make_terminal(repository, first_id, "skipped", now=NOW)
        (second_id,) = await repository.queue_alerts(
            (message,), now=NOW + timedelta(hours=1), dedup_hours=24
        )
        await _make_terminal(
            repository, second_id, "skipped", now=NOW + timedelta(hours=1)
        )

        (third_id,) = await repository.queue_alerts(
            (message,), now=NOW + timedelta(hours=2), dedup_hours=24
        )

        first, second, third = await repository.list_outbox(
            (first_id, second_id, third_id)
        )
        assert [first.status, second.status, third.status] == [
            "skipped",
            "skipped",
            "pending",
        ]
        assert first.idempotency_key == base_key
        assert second.idempotency_key == _requeue_key(base_key, first_id)
        assert third.idempotency_key == _requeue_key(base_key, second_id)
        assert first.payload == second.payload == third.payload == payload
        assert len(second.idempotency_key) <= 256
        assert len(third.idempotency_key) <= 256
    finally:
        await database.dispose()


@pytest.mark.parametrize("terminal_status", ["skipped", "failed"])
async def test_concurrent_terminal_requeue_creates_one_pending_attempt(
    tmp_path, terminal_status: str
) -> None:
    database, repository, preferences, composer = await _services(
        tmp_path, f"concurrent-{terminal_status}.db"
    )
    competing_repository = SqlAlchemyIntelligenceRepository(database.session)
    competing_composer = AlertComposer(
        competing_repository, preferences, RiskPolicy()
    )
    item = _analysis(1)
    try:
        (terminal_id,) = await composer.queue_batch("chat-one", (item,), now=NOW)
        terminal_time = await _make_terminal(
            repository, terminal_id, terminal_status, now=NOW
        )
        terminal = (await repository.list_outbox((terminal_id,)))[0]

        first, second = await asyncio.gather(
            composer.queue_batch(
                "chat-one", (item,), now=terminal_time + timedelta(hours=1)
            ),
            competing_composer.queue_batch(
                "chat-one", (item,), now=terminal_time + timedelta(hours=1)
            ),
        )

        new_ids = first + second
        assert len(new_ids) == 1
        async with database.session() as session:
            rows = (
                await session.scalars(
                    select(DeliveryOutbox).order_by(DeliveryOutbox.id)
                )
            ).all()
        assert len(rows) == 2
        assert rows[0].id == terminal_id
        assert rows[0].status == terminal_status
        assert rows[0].payload == terminal.payload
        assert rows[1].id == new_ids[0]
        assert rows[1].status == "pending"
        assert rows[1].idempotency_key == _requeue_key(
            terminal.idempotency_key, terminal_id
        )
    finally:
        await database.dispose()


async def test_marking_delivery_sent_marks_linked_report_in_same_transaction(
    tmp_path,
) -> None:
    database, repository, _, _ = await _services(tmp_path, "report-sent.db")
    draft = DailyReportComposer().compose(report_date=date(2026, 7, 21), analyses=())
    try:
        report_id = await repository.save_report("chat-one", draft, now=NOW)
        await repository.mark_report_previewed(report_id)
        outbox_id = await repository.queue_report(report_id, now=NOW)
        claim = await repository.claim_delivery_by_id(outbox_id, now=NOW)
        assert claim is not None

        await repository.mark_delivery_sent(claim, message_id="om_report", now=NOW)

        async with database.session() as session:
            report = await session.get(DailyReport, report_id)
            outbox = await session.get(DeliveryOutbox, outbox_id)
        assert report is not None and report.status == "sent"
        assert report.sent_at == NOW
        assert outbox is not None and outbox.status == "sent"
        assert outbox.feishu_message_id == "om_report"
    finally:
        await database.dispose()


@pytest.mark.parametrize("terminal_status", ["skipped", "failed"])
async def test_terminal_daily_report_delivery_can_be_requeued_and_sent(
    tmp_path, terminal_status: str
) -> None:
    database, repository, _, _ = await _services(
        tmp_path, f"report-recovery-{terminal_status}.db"
    )
    draft = DailyReportComposer().compose(report_date=date(2026, 7, 21), analyses=())
    replacement_payload = {"title": f"recovered-{terminal_status}"}
    try:
        report_id = await repository.save_report("chat-one", draft, now=NOW)
        await repository.mark_report_previewed(report_id)
        outbox_id = await repository.queue_report(report_id, now=NOW)
        terminal_time = await _make_terminal(
            repository, outbox_id, terminal_status, now=NOW
        )
        recovery_time = terminal_time + timedelta(hours=1)

        async with database.session.begin() as session:
            report = await session.get(DailyReport, report_id)
            outbox = await session.get(DeliveryOutbox, outbox_id)
            assert report is not None and outbox is not None
            report.report_payload = replacement_payload
            outbox.next_attempt_at = recovery_time + timedelta(days=1)
            outbox.lease_token = "stale-terminal-lease"
            outbox.lease_expires_at = recovery_time + timedelta(minutes=5)
            outbox.feishu_message_id = "stale-message"
            outbox.sent_at = terminal_time

        recovered_id = await repository.queue_report(report_id, now=recovery_time)

        assert recovered_id == outbox_id
        recovered = (await repository.list_outbox((outbox_id,)))[0]
        assert recovered.status == "pending"
        assert recovered.attempt_count == 0
        assert recovered.next_attempt_at is None
        assert recovered.lease_token is None
        assert recovered.lease_expires_at is None
        assert recovered.safe_error_code is None
        assert recovered.feishu_message_id is None
        assert recovered.sent_at is None
        assert recovered.payload == replacement_payload
        assert recovered.created_at == recovery_time

        claim = await repository.claim_delivery_by_id(outbox_id, now=recovery_time)
        assert claim is not None
        assert claim.attempt_count == 1
        assert claim.payload == replacement_payload
        await repository.mark_delivery_sent(
            claim, message_id="om_recovered", now=recovery_time
        )

        async with database.session() as session:
            report = await session.get(DailyReport, report_id)
            outbox = await session.get(DeliveryOutbox, outbox_id)
        assert report is not None and report.status == "sent"
        assert report.sent_at == recovery_time
        assert outbox is not None and outbox.status == "sent"
        assert outbox.feishu_message_id == "om_recovered"
    finally:
        await database.dispose()


async def test_missing_linked_report_rolls_back_sent_transition(tmp_path) -> None:
    database, repository, _, _ = await _services(tmp_path, "missing-report.db")
    try:
        (outbox_id,) = await repository.queue_alerts(
            (
                DeliveryMessage(
                    idempotency_key="daily:chat-one:2026-07-21",
                    group_id="chat-one",
                    kind=MessageKind.DAILY_REPORT,
                    payload={"title": "orphaned report"},
                ),
            ),
            now=NOW,
            dedup_hours=24,
        )
        claim = await repository.claim_delivery_by_id(outbox_id, now=NOW)
        assert claim is not None

        with pytest.raises(RuntimeError, match="linked daily report"):
            await repository.mark_delivery_sent(claim, message_id="om_orphan", now=NOW)

        row = (await repository.list_outbox((outbox_id,)))[0]
        assert row.status == "sending"
        assert row.feishu_message_id is None
    finally:
        await database.dispose()


async def test_skip_delivery_records_controlled_no_binding_code(tmp_path) -> None:
    database, repository, _, _ = await _services(tmp_path, "skip.db")
    try:
        outbox_id = await _queue_raw(repository)
        claim = await repository.claim_delivery_by_id(outbox_id, now=NOW)
        assert claim is not None

        await repository.skip_delivery(claim, "no_active_binding")

        row = (await repository.list_outbox((outbox_id,)))[0]
        assert row.status == "skipped"
        assert row.safe_error_code == "no_active_binding"
        assert row.lease_token is None
    finally:
        await database.dispose()
