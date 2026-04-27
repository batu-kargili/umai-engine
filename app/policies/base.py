"""Policy interfaces and shared helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import List, Optional, Protocol, Sequence, TYPE_CHECKING

from app.models.guardrail import GuardrailSnapshot
from app.models.policy_result import PolicyResult
from app.models.request import ChatMessage, InputPayload, InternalRequest

if TYPE_CHECKING:
    from app.core.llm_client import LLMClient


@dataclass
class PolicyContext:
    """Shared context passed to policy handlers."""

    request: InternalRequest
    guardrail: GuardrailSnapshot
    stop_event: Event
    preflight_flags: List[str] = field(default_factory=list)


class PolicyHandler(Protocol):
    """Protocol for policy handlers used by the pipeline."""

    type: str

    async def run(
        self,
        input_text: str,
        context: PolicyContext,
        config: dict,
        llm_client: Optional["LLMClient"] = None,
    ) -> PolicyResult:
        """Evaluate the policy and return a normalized PolicyResult."""


def extract_target_text(payload: InputPayload, target: str) -> str:
    """Choose the text to scan based on target and phase focus."""

    if target == "ATTACHMENTS":
        return format_artifacts(payload)
    if target == "FULL_CONTEXT":
        return "\n\n".join(
            part for part in [format_full_history(payload.messages), format_artifacts(payload)] if part
        )
    if target == "FULL_HISTORY":
        return "\n\n".join(
            part for part in [format_full_history(payload.messages), format_artifacts(payload)] if part
        )
    return extract_phase_focus_message(payload)


def extract_phase_focus_message(payload: InputPayload) -> str:
    """Select the message indicated by phase_focus, with safe fallbacks."""

    focus_role = "user" if payload.phase_focus == "LAST_USER_MESSAGE" else "assistant"
    for message in reversed(payload.messages):
        if message.role == focus_role:
            return message.content
    return payload.messages[-1].content if payload.messages else ""


def format_full_history(messages: Sequence[ChatMessage]) -> str:
    """Format the full transcript for policy evaluation."""

    return "\n".join(f"{message.role}: {message.content}" for message in messages)


def format_artifacts(payload: InputPayload) -> str:
    """Format artifact content for policy evaluation."""

    parts: list[str] = []
    for artifact in payload.artifacts:
        if not artifact.content:
            continue
        label = artifact.name or artifact.artifact_type
        parts.append(f"artifact:{label}:\n{artifact.content}")
    return "\n\n".join(parts)
