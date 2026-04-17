"""Request models for the AI Engine internal evaluate API."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

GuardrailPhase = Literal[
    "PRE_LLM",
    "POST_LLM",
    "TOOL_INPUT",
    "TOOL_OUTPUT",
    "MCP_REQUEST",
    "MCP_RESPONSE",
    "MEMORY_WRITE",
]


class ChatMessage(BaseModel):
    """Single chat message used as input context.

    Attributes:
        role: Sender role in the conversation.
        content: Raw message text supplied by the caller.
    """

    role: Literal["system", "user", "assistant"]
    content: str


class InputPayload(BaseModel):
    """Structured input payload sent for policy evaluation.

    Attributes:
        messages: Ordered conversation history.
        phase_focus: Which message is the primary evaluation target.
        content_type: Hint describing the format of the content.
        language: Optional ISO language tag for the input.
    """

    messages: List[ChatMessage]
    phase_focus: Literal["LAST_USER_MESSAGE", "LAST_ASSISTANT_MESSAGE"]
    content_type: Literal["text", "markdown", "json"] = "text"
    language: Optional[str] = None
    artifacts: List[dict] = Field(default_factory=list)


class Flags(BaseModel):
    """Execution flags controlling optional pipeline behavior.

    Attributes:
        allow_llm_calls: Whether the pipeline may call LLM-backed policies.
    """

    allow_llm_calls: bool = True


class InternalRequest(BaseModel):
    """Normalized request sent from UMAI Service to the AI Engine.

    Attributes:
        request_id: Caller-provided identifier for traceability.
        timestamp: Request time string from the caller.
        tenant_id: Tenant identifier resolved by UMAI Service.
        environment_id: Environment identifier resolved by UMAI Service.
        project_id: Project identifier resolved by UMAI Service.
        guardrail_id: Guardrail identifier for policy selection.
        guardrail_version: Guardrail version for consistent evaluation.
        phase: Evaluation phase in the guardrail lifecycle.
        input: User and conversation payload for evaluation.
        timeout_ms: Optional overall timeout budget for evaluation.
        flags: Execution flags controlling optional behaviors.
    """

    request_id: str
    timestamp: str

    tenant_id: str
    environment_id: str
    project_id: str
    guardrail_id: str
    guardrail_version: int

    phase: GuardrailPhase

    input: InputPayload
    timeout_ms: Optional[int] = 1500
    flags: Flags = Field(default_factory=Flags)
