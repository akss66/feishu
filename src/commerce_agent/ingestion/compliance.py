"""Runtime compliance enforcement for source collection."""

from commerce_agent.ingestion.models import ComplianceStatus, SourceDefinition


class CompliancePolicyError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"collection rejected: {code}")


class CompliancePolicy:
    def require_collectable(self, source: SourceDefinition) -> None:
        if not source.enabled:
            raise CompliancePolicyError("source_disabled")
        self.require_probeable(source)

    def require_probeable(self, source: SourceDefinition) -> None:
        """Allow a one-shot probe only when the reviewed decision is allowed."""

        if source.compliance is not ComplianceStatus.ALLOWED:
            raise CompliancePolicyError("compliance_not_allowed")
