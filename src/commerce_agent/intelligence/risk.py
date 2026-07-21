import hashlib

from commerce_agent.intelligence.models import (
    AlertQualification,
    AnalysisResult,
    EventType,
    RiskDecision,
    RiskLevel,
    RiskProfile,
    RiskResolution,
)

_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
_HIGH_FLOOR = {EventType.ACCOUNT_ENFORCEMENT, EventType.LISTING_RESTRICTION}
_MEDIUM_FLOOR = {
    EventType.FEES,
    EventType.TAX_COMPLIANCE,
    EventType.LOGISTICS,
    EventType.API_PAYMENT_INCIDENT,
}


def _qualification(
    profile: RiskProfile,
    risk: RiskLevel,
    score: int,
) -> AlertQualification:
    if risk is RiskLevel.LOW:
        return AlertQualification.NONE
    if profile is RiskProfile.CONSERVATIVE:
        return (
            AlertQualification.VERIFIED
            if risk is RiskLevel.HIGH and score >= 85
            else AlertQualification.NONE
        )
    if profile is RiskProfile.DEFAULT:
        return AlertQualification.VERIFIED if score >= 75 else AlertQualification.NONE
    if score >= 75:
        return AlertQualification.VERIFIED
    return AlertQualification.EARLY_SIGNAL if score >= 60 else AlertQualification.NONE


class RiskPolicy:
    def resolve(self, result: AnalysisResult) -> RiskResolution:
        floor = (
            RiskLevel.HIGH
            if result.event_type in _HIGH_FLOOR
            else RiskLevel.MEDIUM
            if result.event_type in _MEDIUM_FLOOR
            else RiskLevel.LOW
        )
        risk = max((result.risk_level, floor), key=_RISK_ORDER.__getitem__)
        conflicts = result.risk_level is RiskLevel.LOW and floor is RiskLevel.HIGH
        return RiskResolution(
            risk_level=risk,
            rule_hits=(f"event_floor:{floor.value}",),
            needs_review=conflicts,
        )

    def assess(
        self,
        result: AnalysisResult,
        evidence_confidence: int,
        profile: RiskProfile = RiskProfile.DEFAULT,
    ) -> RiskDecision:
        resolution = self.resolve(result)
        qualification = (
            AlertQualification.NONE
            if resolution.needs_review
            else _qualification(profile, resolution.risk_level, evidence_confidence)
        )
        return RiskDecision(
            resolution=resolution,
            eligible_for_alert=qualification is not AlertQualification.NONE,
            profile=profile,
            alert_qualification=qualification,
        )


def event_fingerprint(result: AnalysisResult, *, subject: str) -> str:
    normalized_subject = " ".join(subject.casefold().split())
    effective = result.effective_at.isoformat() if result.effective_at else "unknown"
    facts = "|".join(sorted(claim.claim.casefold().strip() for claim in result.rationale))
    raw = "|".join(
        [
            ",".join(sorted(item.value for item in result.platforms)),
            result.event_type.value,
            normalized_subject,
            effective,
            facts,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
