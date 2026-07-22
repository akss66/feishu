from commerce_agent.ingestion.models import TrustTier
from commerce_agent.intelligence.models import AnalysisCandidate, AnalysisResult


class EvidenceScorer:
    def score(
        self,
        candidate: AnalysisCandidate,
        result: AnalysisResult,
        *,
        corroborating_sources: int,
    ) -> int:
        source = 30 if candidate.trust_tier is TrustTier.OFFICIAL else 20
        anchors = 25 if all(claim.quote in candidate.body for claim in result.rationale) else 0
        extraction = (
            15 if len(candidate.body) >= 400 and candidate.language_confidence >= 0.8 else 10
        )
        specificity = 5 * int(bool(result.regions)) + 5 * int(result.effective_at is not None)
        corroboration = 10 if corroborating_sources >= 2 else 0
        schema = 10
        score = min(
            100,
            source + anchors + extraction + specificity + corroboration + schema,
        )
        if candidate.trust_tier is TrustTier.MEDIA and corroborating_sources < 2:
            return min(score, 70)
        return score
