from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Protocol

from commerce_agent.ingestion.models import Platform
from commerce_agent.intelligence.models import RiskLevel

_CHINESE_RUN = re.compile(r"[\u3400-\u9fff]+")
_MAX_QUERY_CHARACTERS = 2_000
_MAX_SEARCH_TERMS = 40
_MAX_RESULTS = 8
_RISK_WEIGHT = {
    RiskLevel.HIGH: 3.0,
    RiskLevel.MEDIUM: 2.0,
    RiskLevel.LOW: 1.0,
}


@dataclass(frozen=True, slots=True)
class CorpusQuery:
    text: str
    now: datetime
    platforms: tuple[Platform, ...] = ()
    regions: tuple[str, ...] = ()
    risk_levels: tuple[RiskLevel, ...] = ()
    since: datetime | None = None
    limit: int = _MAX_RESULTS


@dataclass(frozen=True, slots=True)
class EvidenceDocument:
    document_version_id: int
    analysis_id: int
    source_id: str
    source_name: str
    title: str
    summary_zh: str
    evidence_quotes: tuple[str, ...]
    canonical_url: str
    published_at: datetime | None
    fetched_at: datetime
    platforms: tuple[Platform, ...]
    regions: tuple[str, ...]
    risk_level: RiskLevel
    evidence_confidence: int
    score: float


@dataclass(frozen=True, slots=True)
class CorpusCandidate:
    document_version_id: int
    analysis_id: int
    source_id: str
    source_name: str
    title: str
    summary_zh: str
    evidence_quotes: tuple[str, ...]
    canonical_url: str
    published_at: datetime | None
    fetched_at: datetime
    platforms: tuple[Platform, ...]
    regions: tuple[str, ...]
    risk_level: RiskLevel
    evidence_confidence: int


class CorpusRepository(Protocol):
    async def list_corpus_candidates(
        self,
        *,
        since: datetime,
        until: datetime,
        platforms: tuple[Platform, ...],
        regions: tuple[str, ...],
        risk_levels: tuple[RiskLevel, ...],
        limit: int,
    ) -> tuple[CorpusCandidate, ...]: ...


def search_terms(query: str) -> tuple[str, ...]:
    normalized = " ".join(query[:_MAX_QUERY_CHARACTERS].casefold().split())
    ordered: dict[str, None] = {}
    for part in normalized.split():
        ordered.setdefault(part, None)
        if len(ordered) >= _MAX_SEARCH_TERMS:
            return tuple(ordered)
    for run in _CHINESE_RUN.findall(normalized):
        for size in range(2, min(4, len(run)) + 1):
            for start in range(0, len(run) - size + 1):
                ordered.setdefault(run[start : start + size], None)
                if len(ordered) >= _MAX_SEARCH_TERMS:
                    return tuple(ordered)
    return tuple(ordered)


def lexical_score(query: str, candidate: CorpusCandidate, now: datetime) -> float:
    terms = search_terms(query)
    title = candidate.title.casefold()
    summary = candidate.summary_zh.casefold()
    quotes = tuple(quote.casefold() for quote in candidate.evidence_quotes)
    title_hits = sum(title.count(term) for term in terms)
    summary_hits = sum(summary.count(term) for term in terms)
    quote_hits = sum(quote.count(term) for quote in quotes for term in terms)
    return title_hits * 5.0 + summary_hits * 2.0 + quote_hits * 1.5


class CorpusRetriever:
    def __init__(self, repository: CorpusRepository) -> None:
        self._repository = repository

    async def search(self, query: CorpusQuery) -> tuple[EvidenceDocument, ...]:
        since = query.since or query.now - timedelta(days=30)
        candidates = await self._repository.list_corpus_candidates(
            since=since,
            until=query.now,
            platforms=query.platforms,
            regions=query.regions,
            risk_levels=query.risk_levels,
            limit=100,
        )
        matched = (
            (score, item)
            for item in candidates
            if (score := lexical_score(query.text, item, query.now)) > 0
        )
        ranked = sorted(
            matched,
            key=lambda pair: (
                pair[0],
                _RISK_WEIGHT[pair[1].risk_level],
                pair[1].evidence_confidence,
                max(
                    0.0,
                    3.0
                    - max(
                        0.0,
                        (query.now - pair[1].fetched_at).total_seconds() / 86_400,
                    )
                    / 10,
                ),
                pair[1].fetched_at,
                pair[1].analysis_id,
                pair[1].document_version_id,
            ),
            reverse=True,
        )
        result_limit = max(0, min(query.limit, _MAX_RESULTS))
        return tuple(
            EvidenceDocument(**asdict(item), score=score) for score, item in ranked[:result_limit]
        )
