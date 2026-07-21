from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from commerce_agent.ingestion.models import Platform, TrustTier


class EventType(StrEnum):
    POLICY = "policy"
    FEES = "fees"
    TAX_COMPLIANCE = "tax_compliance"
    LOGISTICS = "logistics"
    LISTING_RESTRICTION = "listing_restriction"
    ACCOUNT_ENFORCEMENT = "account_enforcement"
    API_PAYMENT_INCIDENT = "api_payment_incident"
    MARKET_UPDATE = "market_update"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskProfile(StrEnum):
    CONSERVATIVE = "conservative"
    DEFAULT = "default"
    AGGRESSIVE = "aggressive"


class AlertQualification(StrEnum):
    NONE = "none"
    VERIFIED = "verified"
    EARLY_SIGNAL = "early_signal"


class MessageKind(StrEnum):
    DAILY_REPORT = "daily_report"
    MEDIUM_ALERT_BATCH = "medium_alert_batch"
    HIGH_ALERT = "high_alert"
    QA_ANSWER = "qa_answer"


class EvidenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim: str = Field(min_length=1, max_length=300)
    quote: str = Field(min_length=3, max_length=500)


class ActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: str = Field(min_length=1, max_length=300)
    owner_type: str = Field(min_length=1, max_length=80)
    deadline: datetime | None = None


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    headline_zh: str = Field(min_length=4, max_length=120)
    summary_zh: str = Field(min_length=80, max_length=250)
    event_type: EventType
    platforms: tuple[Platform, ...] = Field(min_length=1)
    regions: tuple[str, ...] = Field(min_length=1)
    affected_seller_types: tuple[str, ...]
    effective_at: datetime | None
    risk_level: RiskLevel
    impact: str = Field(min_length=1, max_length=600)
    rationale: tuple[EvidenceClaim, ...] = Field(min_length=1)
    action_items: tuple[ActionItem, ...]
    uncertainties: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisCandidate:
    job_id: int
    lease_token: str | None
    document_version_id: int
    source_id: str
    source_name: str
    trust_tier: TrustTier
    canonical_url: str
    content_hash: str
    title: str
    body: str
    language: str
    language_confidence: float
    author: str | None
    published_at: datetime | None
    fetched_at: datetime
    platforms: tuple[Platform, ...]
    regions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RiskResolution:
    risk_level: RiskLevel
    rule_hits: tuple[str, ...]
    needs_review: bool


@dataclass(frozen=True, slots=True)
class RiskDecision:
    resolution: RiskResolution
    eligible_for_alert: bool
    profile: RiskProfile
    alert_qualification: AlertQualification

    @property
    def risk_level(self) -> RiskLevel:
        return self.resolution.risk_level

    @property
    def rule_hits(self) -> tuple[str, ...]:
        return self.resolution.rule_hits

    @property
    def needs_review(self) -> bool:
        return self.resolution.needs_review


@dataclass(frozen=True, slots=True)
class RiskProfileChange:
    previous: RiskProfile
    current: RiskProfile


@dataclass(frozen=True, slots=True)
class ScoredAnalysis:
    analysis_id: int
    candidate: AnalysisCandidate
    result: AnalysisResult
    evidence_confidence: int
    resolution: RiskResolution
    event_fingerprint: str


@dataclass(frozen=True, slots=True)
class DeliveryMessage:
    idempotency_key: str
    group_id: str
    kind: MessageKind
    payload: dict[str, Any]
    reply_to_message_id: str | None = None
    reply_in_thread: bool = False


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    id: int
    idempotency_key: str
    group_id: str
    kind: MessageKind
    payload: dict[str, Any]
    reply_to_message_id: str | None
    reply_in_thread: bool
    attempt_count: int
    lease_token: str
