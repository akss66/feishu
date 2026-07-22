from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import Request, Response
from openai import APIConnectionError, APITimeoutError, InternalServerError

import commerce_agent.intelligence.errors as errors_module
from commerce_agent.ingestion.models import Platform, TrustTier
from commerce_agent.intelligence.analyzer import IntelligenceAnalyzer, InvalidModelOutput
from commerce_agent.intelligence.evidence import EvidenceScorer
from commerce_agent.intelligence.models import (
    ActionItem,
    AnalysisCandidate,
    AnalysisResult,
    EventType,
    EvidenceClaim,
    RiskLevel,
)
from commerce_agent.intelligence.repository import StaleLeaseError
from commerce_agent.intelligence.risk import RiskPolicy
from commerce_agent.intelligence.service import AnalysisService, controlled_analysis_error

NOW = datetime(2026, 7, 21, 1, tzinfo=UTC)


def _candidate(job_id: int = 1) -> AnalysisCandidate:
    body = "Seller fees will increase on 2026-08-01. " + "Policy detail. " * 35
    return AnalysisCandidate(
        job_id=job_id,
        lease_token=f"lease-{job_id}",
        document_version_id=job_id,
        source_id=f"source-{job_id}",
        source_name="Official seller news",
        trust_tier=TrustTier.OFFICIAL,
        canonical_url=f"https://example.test/{job_id}",
        content_hash=f"hash-{job_id}",
        title="Seller fee update",
        body=body,
        language="en",
        language_confidence=0.99,
        author="Platform",
        published_at=NOW,
        fetched_at=NOW,
        platforms=(Platform.EBAY,),
        regions=("global",),
    )


def _result(
    *,
    event_type: EventType = EventType.FEES,
    risk_level: RiskLevel = RiskLevel.LOW,
) -> AnalysisResult:
    return AnalysisResult(
        headline_zh="eBay 全球卖家费用政策更新",
        summary_zh=(
            "eBay 发布卖家费用政策调整说明，卖家需要核对适用站点、商品分类、生效日期与账户范围，"
            "重新测算商品毛利和活动预算，并在调整定价或运营策略前逐项复核官方原文规则，"
            "同时将结论同步给财务和相关负责人，持续跟踪后续说明以及尚未确认的实施细节。"
        ),
        event_type=event_type,
        platforms=(Platform.EBAY,),
        regions=("global",),
        affected_seller_types=(),
        effective_at=datetime(2026, 8, 1, tzinfo=UTC),
        risk_level=risk_level,
        impact="费用变化可能影响商品毛利和定价策略。",
        rationale=(
            EvidenceClaim(
                claim="卖家费用将在生效日上涨",
                quote="Seller fees will increase on 2026-08-01.",
            ),
        ),
        action_items=(ActionItem(action="复核成本和定价", owner_type="运营"),),
        uncertainties=("适用卖家范围未知",),
        tags=("费用",),
    )


class FakeRepository:
    def __init__(self, claims: list[AnalysisCandidate]) -> None:
        self.claims = claims.copy()
        self.completed: list[tuple[AnalysisCandidate, AnalysisResult, int, RiskLevel]] = []
        self.failures: list[tuple[int, str]] = []

    async def claim_next(self, *, now: datetime) -> AnalysisCandidate | None:
        del now
        return self.claims.pop(0) if self.claims else None

    async def count_corroborating_sources(
        self,
        fingerprint: str,
        claim: AnalysisCandidate,
        *,
        batch_claims: tuple[AnalysisCandidate, ...] = (),
    ) -> int:
        del fingerprint
        return len({item.source_id for item in (claim, *batch_claims)})

    async def complete_analysis(
        self,
        claim: AnalysisCandidate,
        result: AnalysisResult,
        evidence_confidence: int,
        event_fingerprint: str,
        *,
        risk_level: RiskLevel,
        now: datetime,
        model_name: str,
    ) -> int:
        del event_fingerprint, now, model_name
        self.completed.append((claim, result, evidence_confidence, risk_level))
        return len(self.completed)

    async def fail_analysis(
        self, claim: AnalysisCandidate, error_code: str, *, now: datetime
    ) -> None:
        del now
        self.failures.append((claim.job_id, error_code))


class StaticAnalyzer:
    def __init__(self, result: AnalysisResult) -> None:
        self.result = result

    async def analyze(self, candidate: AnalysisCandidate) -> AnalysisResult:
        del candidate
        return self.result


class RaisingAnalyzer:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def analyze(self, candidate: AnalysisCandidate) -> AnalysisResult:
        del candidate
        raise self.error


class FakeGateway:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses.copy()
        self._index = 0
        self.call_count = 0

    async def complete_json(
        self, system_prompt: str, user_payload: dict[str, object]
    ) -> str:
        del system_prompt, user_payload
        self.call_count += 1
        if not self.responses:
            raise AssertionError("the model gateway must not be called")
        index = min(self._index, len(self.responses) - 1)
        self._index += 1
        return self.responses[index]


class BlockingAnalyzer:
    def __init__(self, result: AnalysisResult) -> None:
        self.result = result
        self.active = 0
        self.maximum_active = 0
        self.two_started = asyncio.Event()
        self.release = asyncio.Event()

    async def analyze(self, candidate: AnalysisCandidate) -> AnalysisResult:
        del candidate
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.active == 2:
            self.two_started.set()
        try:
            await self.release.wait()
            return self.result
        finally:
            self.active -= 1


def _service(repository: FakeRepository, analyzer: Any) -> AnalysisService:
    return AnalysisService(
        repository,
        analyzer,
        EvidenceScorer(),
        RiskPolicy(),
        concurrency=2,
        model_name="test-model",
        clock=lambda: NOW,
    )


async def test_drain_completes_one_result_once_for_one_version() -> None:
    repository = FakeRepository([_candidate()])
    service = _service(repository, StaticAnalyzer(_result()))

    first = await service.drain(limit=10)
    second = await service.drain(limit=10)

    assert (first.claimed, first.succeeded, first.failed) == (1, 1, 0)
    assert len(first.completed) == 1
    assert (second.claimed, second.succeeded, second.failed) == (0, 0, 0)
    assert len(repository.completed) == 1


async def test_invalid_output_uses_controlled_error_and_only_three_attempts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = FakeRepository([_candidate()])
    gateway = FakeGateway(["bad", "still bad"])
    service = _service(repository, IntelligenceAnalyzer(gateway))

    with caplog.at_level(logging.WARNING):
        batch = await service.drain(limit=10)

    assert batch.failed == 1
    assert batch.error_codes == ("invalid_model_output",)
    assert gateway.call_count == 3
    assert repository.failures == [(1, "invalid_model_output")]
    assert "validation_code=invalid_json" in caplog.text
    assert "validation_issues=$:json_invalid" in caplog.text


async def test_drain_never_exceeds_configured_concurrency() -> None:
    repository = FakeRepository([_candidate(job_id) for job_id in range(1, 6)])
    analyzer = BlockingAnalyzer(_result())
    service = _service(repository, analyzer)

    task = asyncio.create_task(service.drain(limit=5))
    await asyncio.wait_for(analyzer.two_started.wait(), timeout=1)
    assert analyzer.maximum_active == 2
    analyzer.release.set()

    batch = await task
    assert batch.succeeded == 5
    assert analyzer.maximum_active == 2


async def test_analysis_uses_resolved_floor_without_overwriting_model_result() -> None:
    repository = FakeRepository([_candidate()])
    result = _result(
        event_type=EventType.ACCOUNT_ENFORCEMENT,
        risk_level=RiskLevel.LOW,
    )

    batch = await _service(repository, StaticAnalyzer(result)).drain(limit=1)

    assert batch.completed[0].resolution.risk_level is RiskLevel.HIGH
    assert repository.completed[0][3] is RiskLevel.HIGH
    assert repository.completed[0][1].risk_level is RiskLevel.LOW
    assert not hasattr(batch.completed[0].resolution, "profile")


async def test_same_fingerprint_batch_sources_receive_cross_source_evidence_points() -> None:
    repository = FakeRepository([_candidate(1), _candidate(2)])

    batch = await _service(repository, StaticAnalyzer(_result())).drain(limit=2)

    assert [item.evidence_confidence for item in batch.completed] == [100, 100]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InvalidModelOutput("private model output"), "invalid_model_output"),
        (TimeoutError("private timeout detail"), "model_timeout"),
        (ConnectionError("private provider detail"), "model_unavailable"),
        (StaleLeaseError("private lease token"), "stale_lease"),
        (RuntimeError("private unexpected detail"), "unexpected_analysis_error"),
    ],
)
def test_controlled_analysis_error_has_a_fixed_allowlist(
    error: Exception, expected: str
) -> None:
    assert controlled_analysis_error(error) == expected


def test_oversized_analysis_input_maps_to_controlled_code() -> None:
    assert (
        controlled_analysis_error(errors_module.OversizedAnalysisInput())
        == "input_too_large"
    )


async def test_long_input_is_bounded_and_completes_without_body_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_marker = "SECRET-LONG-ARTICLE"
    base = _candidate()
    candidate = replace(
        base,
        body=base.body + "x" * 60_000 + secret_marker,
    )
    repository = FakeRepository([candidate])
    result = _result().model_copy(update={"effective_at": None})
    gateway = FakeGateway([result.model_dump_json()])

    with caplog.at_level(logging.WARNING):
        batch = await _service(repository, IntelligenceAnalyzer(gateway)).drain(limit=1)

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert batch.error_codes == ()
    assert batch.succeeded == 1
    assert repository.failures == []
    assert gateway.call_count == 1
    assert secret_marker not in rendered


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            APITimeoutError(request=Request("POST", "https://provider.invalid")),
            "model_timeout",
        ),
        (
            APIConnectionError(request=Request("POST", "https://provider.invalid")),
            "model_unavailable",
        ),
        (
            InternalServerError(
                "provider failed",
                response=Response(
                    503,
                    request=Request("POST", "https://provider.invalid"),
                ),
                body=None,
            ),
            "model_unavailable",
        ),
    ],
)
def test_provider_exceptions_map_to_controlled_codes(
    error: Exception, expected: str
) -> None:
    assert controlled_analysis_error(error) == expected


async def test_stale_lease_is_reported_without_mutating_the_reclaimed_job() -> None:
    repository = FakeRepository([_candidate()])

    batch = await _service(
        repository, RaisingAnalyzer(StaleLeaseError("reclaimed lease"))
    ).drain(limit=1)

    assert batch.error_codes == ("stale_lease",)
    assert repository.failures == []


async def test_failure_log_excludes_exception_message_and_article_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidate = replace(_candidate(), body="SECRET ARTICLE BODY")
    repository = FakeRepository([candidate])
    service = _service(
        repository,
        RaisingAnalyzer(RuntimeError("SECRET MODEL OUTPUT credential group-123")),
    )

    with caplog.at_level(logging.WARNING):
        await service.drain(limit=1)

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in rendered
    assert "job_id=1" in rendered
    assert "elapsed_ms=" in rendered
    assert "SECRET" not in rendered
    assert "credential" not in rendered
    assert "group-123" not in rendered


async def test_non_positive_limit_claims_nothing() -> None:
    repository = FakeRepository([_candidate()])

    batch = await _service(repository, StaticAnalyzer(_result())).drain(limit=0)

    assert (batch.claimed, batch.succeeded, batch.failed) == (0, 0, 0)
    assert len(repository.claims) == 1


def test_service_rejects_non_positive_concurrency() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        AnalysisService(
            FakeRepository([]),
            StaticAnalyzer(_result()),
            EvidenceScorer(),
            RiskPolicy(),
            concurrency=0,
            model_name="test-model",
        )
