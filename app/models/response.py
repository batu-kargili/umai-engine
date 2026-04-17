"""Response models returned by the AI Engine internal evaluate API."""

from typing import List, Optional

from pydantic import BaseModel, Field


class Decision(BaseModel):
    """Final decision produced by the policy pipeline.

    Attributes:
        action: Action label such as ALLOW, BLOCK, or FLAG.
        allowed: Convenience boolean that mirrors the action.
        severity: Severity level associated with the decision.
        reason: Human-readable explanation for the decision.
    """

    action: str
    allowed: bool
    severity: str
    reason: str


class TriggeringPolicyResult(BaseModel):
    """Normalized representation of the policy that triggered the decision.

    Attributes:
        policy_id: Policy identifier.
        type: Policy type (HEURISTIC or CONTEXT_AWARE).
        name: Display name for the policy.
        status: Policy result status such as ALLOW or BLOCK.
        severity: Policy-specific severity classification.
        score: Optional numeric score returned by a policy.
        details: Arbitrary policy result metadata for diagnostics.
        latency_ms: Latency for this policy execution.
    """

    policy_id: str
    type: str
    name: str
    status: str
    severity: str
    score: Optional[float] = None
    details: dict
    latency_ms: float


class LatencyInfo(BaseModel):
    """Timing information for the request lifecycle in milliseconds.

    Attributes:
        total: End-to-end evaluation latency.
        preflight: Optional preflight heuristic latency.
    """

    total: float
    preflight: Optional[float] = None


class ErrorInfo(BaseModel):
    """Structured error information for internal or downstream failures.

    Attributes:
        type: Machine-readable error type.
        source: Subsystem or component that raised the error.
        message: Optional human-readable message.
        retryable: Whether a caller could safely retry.
    """

    type: str
    source: Optional[str] = None
    message: Optional[str] = None
    retryable: Optional[bool] = None


class InternalResponse(BaseModel):
    """Internal response returned to UMAI Service after evaluation.

    Attributes:
        request_id: Request identifier echoed from the input.
        tenant_id: Tenant identifier echoed from the input.
        environment_id: Environment identifier echoed from the input.
        project_id: Project identifier echoed from the input.
        guardrail_id: Guardrail identifier echoed from the input.
        guardrail_version: Guardrail version echoed from the input.
        phase: Evaluation phase echoed from the input.
        decision: Final policy decision for the request.
        triggering_policy: Policy result that led to the decision.
        output_modifications: Suggested redactions/transformations when action modifies output.
        latency_ms: Timing information for evaluation.
        errors: Collected error information during evaluation.
    """

    request_id: str
    tenant_id: str
    environment_id: str
    project_id: str
    guardrail_id: str
    guardrail_version: int
    phase: str

    decision: Decision
    triggering_policy: Optional[TriggeringPolicyResult] = None
    output_modifications: Optional[dict] = None
    latency_ms: LatencyInfo
    errors: List[ErrorInfo] = Field(default_factory=list)
