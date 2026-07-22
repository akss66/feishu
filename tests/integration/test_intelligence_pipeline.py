from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from commerce_agent.domain import InboundMessage
from commerce_agent.ingestion.dedupe import fingerprint_document
from commerce_agent.ingestion.extract import ContentExtractor, LanguageDetection
from commerce_agent.ingestion.models import (
    CollectedItem,
    CollectorKind,
    ComplianceStatus,
    Platform,
    SourceDefinition,
    TrustTier,
)
from commerce_agent.intelligence.analyzer import IntelligenceAnalyzer
from commerce_agent.intelligence.delivery import DeliveryWorker, FeishuMessageRenderer
from commerce_agent.intelligence.evidence import EvidenceScorer
from commerce_agent.intelligence.models import MessageKind, RiskProfile
from commerce_agent.intelligence.qa import QaService, ThreadContextStore
from commerce_agent.intelligence.reports import (
    AlertComposer,
    DailyReportComposer,
    DailyReportService,
)
from commerce_agent.intelligence.repository import SqlAlchemyIntelligenceRepository
from commerce_agent.intelligence.retrieval import CorpusRetriever
from commerce_agent.intelligence.risk import RiskPolicy
from commerce_agent.intelligence.service import AnalysisService
from commerce_agent.intelligence_cli import ProductionCliApplication
from commerce_agent.persistence.database import Database
from commerce_agent.persistence.group_bindings import SqlAlchemyGroupBindingStore
from commerce_agent.persistence.ingestion import (
    PersistableDocument,
    SqlAlchemyIngestionRepository,
)
from commerce_agent.persistence.intelligence_preferences import (
    SqlAlchemyIntelligencePreferenceStore,
)

NOW = datetime(2026, 7, 21, 12, tzinfo=UTC)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "intelligence"


class FixedLanguageDetector:
    def detect(self, text: str) -> LanguageDetection:
        assert text
        return LanguageDetection("en", 0.95)


class StaticJsonGateway:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = json.dumps(response, ensure_ascii=False)
        self.calls = 0

    async def complete_json(self, system_prompt: str, user_payload: dict[str, object]) -> str:
        assert system_prompt
        assert user_payload
        self.calls += 1
        return self._response


class FakeFeishuPort:
    def __init__(self) -> None:
        self.sent: list[tuple[MessageKind, dict[str, object]]] = []
        self._renderer = FeishuMessageRenderer()

    async def send(self, claim) -> str:
        self.sent.append((claim.kind, self._renderer.render(claim)))
        return f"message-{len(self.sent)}"


@dataclass
class OfflinePipeline:
    database: Database
    ingestion: SqlAlchemyIngestionRepository
    repository: SqlAlchemyIntelligenceRepository
    preferences: SqlAlchemyIntelligencePreferenceStore
    bindings: SqlAlchemyGroupBindingStore
    analysis: AnalysisService
    alerts: AlertComposer
    reports: DailyReportService
    qa: QaService
    delivery: DeliveryWorker
    fake_feishu: FakeFeishuPort
    analysis_gateway: StaticJsonGateway
    qa_gateway: StaticJsonGateway

    async def ingest_fixture(self, filename: str) -> int:
        source = SourceDefinition(
            source_id="allowed-media",
            name="Allowed marketplace publication",
            entry_url="https://news.example.test/fees",
            platforms=(Platform.EBAY,),
            trust_tier=TrustTier.MEDIA,
            collector=CollectorKind.HTML,
            compliance=ComplianceStatus.ALLOWED,
            enabled=True,
            regions=("global",),
            language_hint="en",
            interval_minutes=120,
            terms_url="https://news.example.test/terms",
            robots_url="https://news.example.test/robots.txt",
            reviewed_at=date(2026, 7, 20),
            compliance_notes="public fixture",
            collector_config={"article_selector": "article"},
        )
        await self.ingestion.sync_sources([source])
        extracted = ContentExtractor(FixedLanguageDetector()).extract(
            source,
            CollectedItem(
                url="https://news.example.test/fees/update",
                body=(FIXTURES / filename).read_bytes(),
                content_type="text/html; charset=utf-8",
                published_at=NOW - timedelta(hours=1),
            ),
            fetched_at=NOW,
        )
        fingerprint = fingerprint_document(extracted.canonical_url, extracted.body)
        outcome = await self.ingestion.persist_version(
            PersistableDocument(
                source_id=extracted.source_id,
                canonical_url=fingerprint.canonical_url,
                title=extracted.title,
                body=extracted.body,
                language=extracted.language,
                language_confidence=extracted.language_confidence,
                content_hash=fingerprint.content_hash,
                content_group_hash=fingerprint.content_group_hash,
                fetched_at=extracted.fetched_at,
                author=extracted.author,
                published_at=extracted.published_at,
            )
        )
        return outcome.version_id

    async def aclose(self) -> None:
        await self.database.dispose()


def _analysis_response() -> dict[str, object]:
    return {
        "headline_zh": "平台费用政策出现待核实变化",
        "summary_zh": (
            "公开来源提到部分商品刊登费用可能在政策复核后变化，卖家应先核对适用范围和正式通知，"
            "整理受影响商品与成本项目，再由负责人完成复核；当前信息不足以支持立即调价或其他不可逆操作。"
        ),
        "event_type": "fees",
        "platforms": ["ebay"],
        "regions": ["global"],
        "affected_seller_types": [],
        "effective_at": None,
        "risk_level": "medium",
        "impact": "费用变化可能影响部分刊登的成本测算",
        "rationale": [
            {
                "claim": "来源称部分刊登费用会变化",
                "quote": "Marketplace fees will change for selected listings",
            }
        ],
        "action_items": [
            {
                "action": "立即修改全部商品价格",
                "owner_type": "运营",
                "deadline": None,
            }
        ],
        "uncertainties": ["seller scope unknown"],
        "tags": ["费用", "待核实"],
    }


async def build_offline_pipeline(tmp_path) -> OfflinePipeline:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'offline-intelligence.db'}")
    await database.create_schema()
    ingestion = SqlAlchemyIngestionRepository(database.session)
    repository = SqlAlchemyIntelligenceRepository(database.session)
    preferences = SqlAlchemyIntelligencePreferenceStore(database.session)
    bindings = SqlAlchemyGroupBindingStore(database.session)
    analysis_gateway = StaticJsonGateway(_analysis_response())
    qa_gateway = StaticJsonGateway(
        {"answer": "费用变化可能影响部分刊登的成本测算。[1]", "citations_used": [1]}
    )
    risk = RiskPolicy()
    fake_feishu = FakeFeishuPort()
    return OfflinePipeline(
        database=database,
        ingestion=ingestion,
        repository=repository,
        preferences=preferences,
        bindings=bindings,
        analysis=AnalysisService(
            repository,
            IntelligenceAnalyzer(analysis_gateway),
            EvidenceScorer(),
            risk,
            concurrency=1,
            model_name="offline-fake",
            clock=lambda: NOW,
        ),
        alerts=AlertComposer(
            repository,
            preferences,
            risk,
            default_profile=RiskProfile.DEFAULT,
        ),
        reports=DailyReportService(
            repository,
            DailyReportComposer(ZoneInfo("Asia/Shanghai")),
            preferences,
            timezone=ZoneInfo("Asia/Shanghai"),
            default_profile=RiskProfile.DEFAULT,
            clock=lambda: NOW,
        ),
        qa=QaService(
            CorpusRetriever(repository),
            qa_gateway,
            repository,
            ThreadContextStore(max_turns=6, ttl=timedelta(minutes=30)),
            clock=lambda: NOW,
        ),
        delivery=DeliveryWorker(
            repository,
            fake_feishu,
            bindings=bindings,
            clock=lambda: NOW,
        ),
        fake_feishu=fake_feishu,
        analysis_gateway=analysis_gateway,
        qa_gateway=qa_gateway,
    )


async def test_offline_pipeline_from_article_to_alert_report_qa_and_delivery(
    tmp_path,
) -> None:
    app = await build_offline_pipeline(tmp_path)
    try:
        await app.bindings.bind("test-group")
        await app.preferences.set("test-group", RiskProfile.AGGRESSIVE, now=NOW)
        await app.ingest_fixture("allowed-media-fee-change.html")

        batch = await app.analysis.drain(limit=10)
        alert_ids = await app.alerts.queue_batch("test-group", batch.completed, now=NOW)
        duplicate_ids = await app.alerts.queue_batch(
            "test-group", batch.completed, now=NOW + timedelta(hours=1)
        )
        report = await app.reports.preview("test-group", date(2026, 7, 22))
        report_outbox = await app.reports.queue_previewed("test-group", date(2026, 7, 22))
        qa_outbox = await app.qa.queue_answer(
            InboundMessage(
                chat_id="test-group",
                message_id="message-one",
                thread_id="thread-one",
                text="fee update 会影响哪些刊登？",
            )
        )

        default_ids = await app.alerts.queue_batch("default-group", batch.completed, now=NOW)
        await app.preferences.set("conservative-group", RiskProfile.CONSERVATIVE, now=NOW)
        conservative_ids = await app.alerts.queue_batch(
            "conservative-group", batch.completed, now=NOW
        )

        alert_row = (await app.repository.list_outbox(alert_ids))[0]
        qa_row = (await app.repository.list_outbox((qa_outbox,)))[0]
        delivery = await app.delivery.drain(limit=10)

        assert batch.claimed == batch.succeeded == 1
        assert batch.failed == 0
        assert batch.completed[0].evidence_confidence == 70
        assert app.analysis_gateway.calls == 1
        assert alert_ids and duplicate_ids == ()
        assert default_ids == ()
        assert conservative_ids == ()
        assert alert_row.message_kind == MessageKind.MEDIUM_ALERT_BATCH.value
        assert alert_row.payload["theme"] == "orange"
        assert alert_row.payload["items"][0]["verification_status"] == "early_signal"
        rendered_actions = json.dumps(alert_row.payload["items"][0]["actions"], ensure_ascii=False)
        assert "立即修改全部商品价格" not in rendered_actions
        assert "不可逆" in rendered_actions
        assert report.selected_analysis_ids == (batch.completed[0].analysis_id,)
        assert report_outbox > 0
        assert "[1]" in qa_row.payload["text"]
        assert "https://news.example.test/fees/update" in qa_row.payload["text"]
        assert app.qa_gateway.calls == 1
        assert delivery.sent == 3
        assert delivery.failed == delivery.skipped == 0
        assert {kind for kind, _ in app.fake_feishu.sent} == {
            MessageKind.MEDIUM_ALERT_BATCH,
            MessageKind.DAILY_REPORT,
            MessageKind.QA_ANSWER,
        }
        alert_card = next(
            payload
            for kind, payload in app.fake_feishu.sent
            if kind is MessageKind.MEDIUM_ALERT_BATCH
        )
        assert alert_card["card"]["header"]["template"] == "orange"
    finally:
        await app.aclose()


async def test_repository_health_summary_exposes_only_status_counts(tmp_path) -> None:
    app = await build_offline_pipeline(tmp_path)
    try:
        await app.ingest_fixture("allowed-media-fee-change.html")

        summary = await app.repository.health_summary(now=NOW)

        assert summary == {
            "status": "healthy",
            "analysis_pending": 1,
            "analysis_retry_wait": 0,
            "analysis_failed": 0,
            "outbox_pending": 0,
            "outbox_retry_wait": 0,
            "outbox_failed": 0,
        }
        assert all(isinstance(value, int) for key, value in summary.items() if key != "status")
    finally:
        await app.aclose()


async def test_alert_cli_preview_uses_active_profile_without_queueing(tmp_path) -> None:
    app = await build_offline_pipeline(tmp_path)
    try:
        await app.bindings.bind("test-group")
        await app.preferences.set("test-group", RiskProfile.AGGRESSIVE, now=NOW)
        await app.ingest_fixture("allowed-media-fee-change.html")
        batch = await app.analysis.drain(limit=1)
        assert batch.succeeded == 1
        cli = ProductionCliApplication(
            SimpleNamespace(
                repository=app.repository,
                preferences=app.preferences,
                default_profile=RiskProfile.DEFAULT,
                alerts=app.alerts,
            ),
            app.bindings,
            app.database,
            SimpleNamespace(),
            SimpleNamespace(),
            clock=lambda: NOW,
        )

        summary = await cli.preview_alerts(24)

        assert summary == {
            "status": "previewed",
            "selected": 1,
            "high": 0,
            "medium": 1,
        }
        health = await app.repository.health_summary(now=NOW)
        assert health["outbox_pending"] == 0

        queued_ids = await app.alerts.queue_batch("test-group", batch.completed, now=NOW)
        after_queue = await cli.preview_alerts(24)

        assert queued_ids
        assert after_queue == {
            "status": "previewed",
            "selected": 0,
            "high": 0,
            "medium": 0,
        }
    finally:
        await app.aclose()
