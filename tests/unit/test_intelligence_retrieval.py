from __future__ import annotations

from datetime import UTC, datetime, timedelta

from commerce_agent.ingestion.models import Platform
from commerce_agent.intelligence.models import RiskLevel
from commerce_agent.intelligence.retrieval import (
    CorpusCandidate,
    CorpusQuery,
    CorpusRetriever,
    lexical_score,
    search_terms,
)

NOW = datetime(2026, 7, 21, 1, tzinfo=UTC)


def _candidate(
    candidate_id: int,
    *,
    title: str = "Policy update",
    summary: str = "Seller policy changed",
    quotes: tuple[str, ...] = ("policy changed",),
    fetched_at: datetime = NOW,
    risk_level: RiskLevel = RiskLevel.LOW,
    evidence_confidence: int = 75,
) -> CorpusCandidate:
    return CorpusCandidate(
        document_version_id=candidate_id,
        analysis_id=candidate_id + 100,
        source_id=f"source-{candidate_id}",
        source_name=f"Source {candidate_id}",
        title=title,
        summary_zh=summary,
        evidence_quotes=quotes,
        canonical_url=f"https://example.com/{candidate_id}",
        published_at=fetched_at,
        fetched_at=fetched_at,
        platforms=(Platform.EBAY,),
        regions=("global",),
        risk_level=risk_level,
        evidence_confidence=evidence_confidence,
    )


class _RepositorySpy:
    def __init__(self, candidates: tuple[CorpusCandidate, ...]) -> None:
        self.candidates = candidates
        self.request: dict[str, object] | None = None

    async def list_corpus_candidates(self, **request: object) -> tuple[CorpusCandidate, ...]:
        self.request = request
        return self.candidates


def test_search_terms_normalizes_english_and_adds_ordered_chinese_fragments() -> None:
    assert search_terms("  FEE   费用变化  ") == (
        "fee",
        "费用变化",
        "费用",
        "用变",
        "变化",
        "费用变",
        "用变化",
    )


def test_search_terms_caps_adversarial_input_at_forty_terms() -> None:
    terms = search_terms("".join(chr(0x4E00 + index) for index in range(100)))

    assert len(terms) == 40
    assert len(set(terms)) == len(terms)


def test_title_match_beats_summary_only_match_despite_recency() -> None:
    title_match = _candidate(
        1,
        title="账户停用通知",
        summary="其他变化",
        quotes=(),
        fetched_at=NOW - timedelta(days=20),
    )
    summary_match = _candidate(
        2,
        title="平台通知",
        summary="账户停用",
        quotes=(),
        fetched_at=NOW,
    )

    assert lexical_score("账户停用", title_match, NOW) > lexical_score(
        "账户停用", summary_match, NOW
    )


async def test_search_applies_default_window_filters_and_eight_item_cap() -> None:
    candidates = tuple(_candidate(index) for index in range(1, 11))
    repository = _RepositorySpy(candidates)
    retriever = CorpusRetriever(repository)

    results = await retriever.search(
        CorpusQuery(
            text="policy",
            now=NOW,
            platforms=(Platform.EBAY,),
            regions=("global",),
            risk_levels=(RiskLevel.LOW,),
            limit=99,
        )
    )

    assert repository.request == {
        "since": NOW - timedelta(days=30),
        "until": NOW,
        "platforms": (Platform.EBAY,),
        "regions": ("global",),
        "risk_levels": (RiskLevel.LOW,),
        "limit": 100,
    }
    assert len(results) == 8
    assert all(result.score > 0 for result in results)
    assert results[0].analysis_id == 110
    assert results[0].document_version_id == 10


async def test_search_ranks_risk_then_confidence_then_recency_deterministically() -> None:
    candidates = (
        _candidate(1, risk_level=RiskLevel.LOW, evidence_confidence=99),
        _candidate(2, risk_level=RiskLevel.HIGH, evidence_confidence=60),
        _candidate(3, risk_level=RiskLevel.MEDIUM, evidence_confidence=80),
        _candidate(4, risk_level=RiskLevel.MEDIUM, evidence_confidence=90),
        _candidate(5, risk_level=RiskLevel.MEDIUM, evidence_confidence=90),
    )
    retriever = CorpusRetriever(_RepositorySpy(candidates))

    results = await retriever.search(CorpusQuery(text="policy", now=NOW))

    assert [result.document_version_id for result in results] == [2, 5, 4, 3, 1]


async def test_search_honors_zero_limit_without_query_or_content_persistence() -> None:
    repository = _RepositorySpy((_candidate(1),))

    results = await CorpusRetriever(repository).search(
        CorpusQuery(text="do not persist this query", now=NOW, limit=0)
    )

    assert results == ()
    assert repository.request is not None
    assert "text" not in repository.request
