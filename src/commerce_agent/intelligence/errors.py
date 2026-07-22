class EmptyModelOutput(RuntimeError):
    """Safe, provider-independent signal for an empty structured response."""


class OversizedAnalysisInput(RuntimeError):
    """Safe signal that analysis input exceeds the fixed character limit."""
