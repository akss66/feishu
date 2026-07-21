from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic

from openai import APIConnectionError, APIStatusError, APITimeoutError

from commerce_agent.intelligence.analyzer import IntelligenceAnalyzer, InvalidModelOutput
from commerce_agent.intelligence.evidence import EvidenceScorer
from commerce_agent.intelligence.models import (
    AnalysisCandidate,
    AnalysisResult,
    ScoredAnalysis,
)
from commerce_agent.intelligence.repository import (
    SqlAlchemyIntelligenceRepository,
    StaleLeaseError,
)
from commerce_agent.intelligence.risk import RiskPolicy, event_fingerprint

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnalysisBatch:
    claimed: int
    succeeded: int
    failed: int
    completed: tuple[ScoredAnalysis, ...]
    error_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Analyzed:
    claim: AnalysisCandidate
    result: AnalysisResult
    fingerprint: str


def controlled_analysis_error(error: Exception) -> str:
    if isinstance(error, InvalidModelOutput):
        return "invalid_model_output"
    if isinstance(error, (TimeoutError, APITimeoutError)):
        return "model_timeout"
    if isinstance(error, StaleLeaseError):
        return "stale_lease"
    if isinstance(error, (ConnectionError, OSError, APIConnectionError, APIStatusError)):
        return "model_unavailable"
    return "unexpected_analysis_error"


class AnalysisService:
    def __init__(
        self,
        repository: SqlAlchemyIntelligenceRepository,
        analyzer: IntelligenceAnalyzer,
        evidence: EvidenceScorer,
        risk: RiskPolicy,
        *,
        concurrency: int,
        model_name: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        self._repository = repository
        self._analyzer = analyzer
        self._evidence = evidence
        self._risk = risk
        self._concurrency = concurrency
        self._model_name = model_name
        self._clock = clock

    async def drain(self, *, limit: int) -> AnalysisBatch:
        claims: list[AnalysisCandidate] = []
        now = self._clock()
        for _ in range(max(0, limit)):
            claim = await self._repository.claim_next(now=now)
            if claim is None:
                break
            claims.append(claim)

        semaphore = asyncio.Semaphore(self._concurrency)

        async def handle_failure(
            claim: AnalysisCandidate,
            error: Exception,
            started_at: float,
        ) -> str:
            code = controlled_analysis_error(error)
            _log_analysis_failure(error, claim.job_id, started_at)
            if code == "stale_lease":
                return code
            try:
                await self._repository.fail_analysis(
                    claim,
                    code,
                    now=self._clock(),
                )
            except asyncio.CancelledError:
                raise
            except Exception as failure_error:
                _log_analysis_failure(failure_error, claim.job_id, started_at)
                return controlled_analysis_error(failure_error)
            return code

        async def analyze_one(claim: AnalysisCandidate) -> _Analyzed | str:
            async with semaphore:
                started_at = monotonic()
                try:
                    result = await self._analyzer.analyze(claim)
                    fingerprint = event_fingerprint(result, subject=result.headline_zh)
                    return _Analyzed(claim, result, fingerprint)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    return await handle_failure(claim, error, started_at)

        analyzed_results = await asyncio.gather(*(analyze_one(claim) for claim in claims))
        grouped_claims: dict[str, list[AnalysisCandidate]] = {}
        for item in analyzed_results:
            if isinstance(item, _Analyzed):
                grouped_claims.setdefault(item.fingerprint, []).append(item.claim)

        async def score_one(item: _Analyzed) -> ScoredAnalysis | str:
            async with semaphore:
                started_at = monotonic()
                try:
                    corroborating = await self._repository.count_corroborating_sources(
                        item.fingerprint,
                        item.claim,
                        batch_claims=tuple(grouped_claims[item.fingerprint]),
                    )
                    score = self._evidence.score(
                        item.claim,
                        item.result,
                        corroborating_sources=corroborating,
                    )
                    resolution = self._risk.resolve(item.result)
                    analysis_id = await self._repository.complete_analysis(
                        item.claim,
                        item.result,
                        score,
                        item.fingerprint,
                        risk_level=resolution.risk_level,
                        now=self._clock(),
                        model_name=self._model_name,
                    )
                    return ScoredAnalysis(
                        analysis_id,
                        item.claim,
                        item.result,
                        score,
                        resolution,
                        item.fingerprint,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    return await handle_failure(item.claim, error, started_at)

        scored_results = await asyncio.gather(
            *(score_one(item) for item in analyzed_results if isinstance(item, _Analyzed))
        )
        scored = iter(scored_results)
        results = tuple(
            next(scored) if isinstance(item, _Analyzed) else item
            for item in analyzed_results
        )
        completed = tuple(item for item in results if isinstance(item, ScoredAnalysis))
        errors = tuple(item for item in results if isinstance(item, str))
        return AnalysisBatch(
            claimed=len(claims),
            succeeded=len(completed),
            failed=len(errors),
            completed=completed,
            error_codes=errors,
        )


def _log_analysis_failure(error: Exception, job_id: int, started_at: float) -> None:
    elapsed_ms = max(0, round((monotonic() - started_at) * 1000))
    logger.warning(
        "analysis_failed error_type=%s job_id=%s elapsed_ms=%s",
        type(error).__name__,
        job_id,
        elapsed_ms,
    )
