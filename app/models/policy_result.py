"""Internal policy result types used during pipeline evaluation."""

from dataclasses import dataclass
from typing import Optional

from app.models.response import TriggeringPolicyResult


@dataclass
class PolicyResult:
    """Normalized policy result produced by individual policy handlers.

    Attributes:
        policy_id: Policy identifier.
        type: Policy type (HEURISTIC or CONTEXT_AWARE).
        name: Human-readable policy name.
        status: Policy result status (ALLOW, BLOCK, FLAG, ERROR).
        severity: Severity level associated with the result.
        details: Free-form metadata captured by the policy.
        latency_ms: Policy execution time in milliseconds.
        score: Optional numeric score from the policy.
    """

    policy_id: str
    type: str
    name: str
    status: str
    severity: str
    details: dict
    latency_ms: float
    score: Optional[float] = None

    def to_triggering_policy(self) -> TriggeringPolicyResult:
        """Convert to the response model used in the API output.

        Returns:
            TriggeringPolicyResult: A public-facing representation of this
            policy result suitable for inclusion in InternalResponse.
        """

        return TriggeringPolicyResult(
            policy_id=self.policy_id,
            type=self.type,
            name=self.name,
            status=self.status,
            severity=self.severity,
            score=self.score,
            details=self.details,
            latency_ms=self.latency_ms,
        )
