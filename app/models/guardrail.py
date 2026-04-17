"""Policy and guardrail configuration models for the AI Engine."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

PolicyType = Literal["HEURISTIC", "CONTEXT_AWARE"]
LLMAuthType = Literal["none", "bearer", "header"]
GuardrailPhase = Literal[
    "PRE_LLM",
    "POST_LLM",
    "TOOL_INPUT",
    "TOOL_OUTPUT",
    "MCP_REQUEST",
    "MCP_RESPONSE",
    "MEMORY_WRITE",
]


class HeuristicRule(BaseModel):
    """Single rule evaluated by heuristic policies.

    Attributes:
        id: Stable rule identifier.
        mode: Rule matching mode (REGEX or EXACT).
        pattern: Pattern or literal string to match against.
        block_on_match: Whether a match should block the request.
    """

    id: str
    mode: Literal["REGEX", "EXACT"]
    pattern: str
    block_on_match: bool = True
    step_up_on_match: bool = False
    redact_on_match: bool = False
    replacement: str = "[REDACTED]"


class HeuristicConfig(BaseModel):
    """Configuration for heuristic policy evaluation.

    Attributes:
        target: Which part of the input is scanned.
        rules: Ordered list of heuristic rules.
        max_length: Optional maximum length for evaluation.
    """

    target: Literal["LAST_MESSAGE", "FULL_HISTORY"] = "LAST_MESSAGE"
    rules: List[HeuristicRule]
    max_length: Optional[int] = None


class ContextAwareOutputSchema(BaseModel):
    """Field mapping used to parse JSON responses from context-aware LLMs.

    Attributes:
        violation_field: Field name for violation flag in LLM output.
        category_field: Field name for policy category in LLM output.
        confidence_field: Field name for confidence in LLM output.
        rationale_field: Field name for rationale in LLM output.
    """

    violation_field: str
    category_field: str
    confidence_field: str
    rationale_field: str


class ContextAwareConfig(BaseModel):
    """Configuration for context-aware policy evaluation using an LLM.

    Attributes:
        target: Which part of the input is used as LLM content.
        instructions: System instructions for the LLM prompt.
        definitions_and_category_map: Guardrail definitions and categories.
        examples: Prompt examples to steer the classifier.
        output_schema: Output field mapping for JSON parsing.
        min_confidence_for_block: Minimum confidence needed to block.
        fail_closed_on_error: Whether to block on LLM errors.
    """

    target: Literal["LAST_MESSAGE", "FULL_HISTORY"] = "LAST_MESSAGE"
    instructions: str
    definitions_and_category_map: str
    examples: str
    output_schema: ContextAwareOutputSchema
    min_confidence_for_block: Literal["low", "medium", "high"] = "medium"
    fail_closed_on_error: bool = True
    step_up_categories: List[str] = Field(default_factory=list)


class Policy(BaseModel):
    """Policy definition referenced by a guardrail snapshot.

    Attributes:
        id: Stable policy identifier.
        type: Policy type used to select the handler.
        name: Human-readable policy name.
        enabled: Whether the policy is active.
        phases: Phases in which this policy applies.
        config: Policy-specific configuration payload.
    """

    id: str
    type: PolicyType
    name: str
    enabled: bool = True
    phases: List[GuardrailPhase]
    config: Dict[str, Any]


class LLMAuthConfig(BaseModel):
    """Authentication metadata for OpenAI-compatible inference endpoints."""

    type: LLMAuthType = "bearer"
    secret_env: str | None = None
    header_name: str | None = None

    @model_validator(mode="after")
    def validate_auth(self) -> "LLMAuthConfig":
        if self.type == "none":
            return self
        if not self.secret_env or not self.secret_env.strip():
            raise ValueError("auth.secret_env is required unless auth.type is 'none'")
        if self.type == "header" and (not self.header_name or not self.header_name.strip()):
            raise ValueError("auth.header_name is required when auth.type is 'header'")
        return self


class LLMConfig(BaseModel):
    """LLM connection details required for context-aware policies.

    Attributes:
        provider: Provider identifier used for routing.
        base_url: Base URL for the OpenAI-compatible endpoint.
        model: Model name to invoke.
        timeout_ms: Request timeout budget for LLM calls.
    """

    provider: str
    base_url: str
    model: str
    timeout_ms: int = 2000
    auth: LLMAuthConfig | None = None


class GuardrailSnapshot(BaseModel):
    """Resolved guardrail configuration attached to an internal request.

    Attributes:
        guardrail_id: Guardrail identifier.
        version: Guardrail version used in evaluation.
        mode: Enforcement mode (ENFORCE or MONITOR).
        phases: Phases supported by this guardrail.
        preflight: Heuristic preflight configuration.
        policies: Ordered list of policies to evaluate.
        llm_config: LLM configuration for context-aware policies.
    """

    guardrail_id: str
    version: int
    mode: Literal["ENFORCE", "MONITOR"]
    phases: List[GuardrailPhase]

    preflight: HeuristicConfig
    policies: List[Policy]
    llm_config: LLMConfig
    signature: str | None = None
    key_id: str | None = None
