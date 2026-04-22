"""Context-aware policy implementation using an LLM classifier."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

from pydantic import ValidationError

from app.core.env import load_env
from app.core.llm_client import LLMClient
from app.models.guardrail import ContextAwareConfig
from app.models.policy_result import PolicyResult
from app.policies.base import PolicyContext, extract_target_text

logger = logging.getLogger(__name__)

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


class ContextAwarePolicy:
    """Use an LLM to classify input against guardrail definitions."""

    type = "CONTEXT_AWARE"

    def __init__(self, policy_id: str, name: str) -> None:
        self.policy_id = policy_id
        self.name = name

    async def run(
        self,
        input_text: str,
        context: PolicyContext,
        config: dict,
        llm_client: Optional[LLMClient] = None,
    ) -> PolicyResult:
        """Run the LLM classifier and map its output to a PolicyResult."""

        start = time.perf_counter()
        if context.stop_event.is_set():
            return PolicyResult(
                policy_id=self.policy_id,
                type=self.type,
                name=self.name,
                status="ALLOW",
                severity="LOW",
                details={"skipped": True},
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        try:
            parsed_config = ContextAwareConfig(**config)
        except ValidationError as exc:
            return self._error_result(
                start,
                context,
                None,
                "invalid_config",
                str(exc),
            )

        if not input_text:
            input_text = extract_target_text(context.request.input, parsed_config.target)

        try:
            messages = self._build_prompt(parsed_config, input_text)
            response_text = await self._call_llm(
                context,
                parsed_config,
                messages,
                llm_client=llm_client,
            )
            classification = self._parse_response(parsed_config, response_text)
        except Exception as exc:
            logger.warning("Context-aware policy failed: %s", exc)
            return self._error_result(
                start,
                context,
                parsed_config,
                "llm_error",
                str(exc),
            )

        violation = int(classification.get("violation", 1))
        confidence = str(classification.get("confidence", "low")).strip().lower()
        min_rank = _CONFIDENCE_RANK.get(parsed_config.min_confidence_for_block, 1)
        confidence_rank = _CONFIDENCE_RANK.get(confidence, 0)
        policy_category = classification.get("policy_category") or (
            "SAFE" if violation == 0 else "UNSPECIFIED"
        )
        rationale = classification.get("rationale", "")

        details = {
            "violation": violation,
            "policy_category": policy_category,
            "confidence": confidence,
            "rationale": rationale,
            "raw": classification.get("raw"),
        }
        step_up_categories = {value.strip().lower() for value in parsed_config.step_up_categories}
        needs_step_up = policy_category and str(policy_category).strip().lower() in step_up_categories

        if violation == 0:
            status = "ALLOW"
            severity = "LOW"
        elif needs_step_up:
            status = "STEP_UP_APPROVAL"
            severity = "HIGH"
            context.stop_event.set()
        elif confidence_rank >= min_rank:
            status = "BLOCK"
            severity = _severity_from_confidence(confidence)
            context.stop_event.set()
        else:
            status = "FLAG"
            severity = "MEDIUM"

        return PolicyResult(
            policy_id=self.policy_id,
            type=self.type,
            name=self.name,
            status=status,
            severity=severity,
            details=details,
            latency_ms=(time.perf_counter() - start) * 1000,
        )

    def _build_prompt(
        self, config: ContextAwareConfig, input_text: str
    ) -> list[Dict[str, str]]:
        """Compose system and user messages for the classifier prompt."""

        parts = [
            config.instructions.strip(),
            config.definitions_and_category_map.strip(),
            config.examples.strip(),
        ]
        system_prompt = "\n\n".join(part for part in parts if part)
        user_prompt = f"Content: {input_text}\nAnswer:"
        messages = [{"role": "system", "content": system_prompt}]
        if user_prompt.strip():
            messages.append({"role": "user", "content": user_prompt})
        return messages

    async def _call_llm(
        self,
        context: PolicyContext,
        config: ContextAwareConfig,
        messages: list[Dict[str, str]],
        llm_client: Optional[LLMClient],
    ) -> str:
        """Invoke the configured LLM endpoint and return raw content."""

        headers = _build_llm_headers(context.guardrail.llm_config)
        client = llm_client or LLMClient(
            base_url=context.guardrail.llm_config.base_url,
            headers=headers,
        )
        timeout_s = context.guardrail.llm_config.timeout_ms / 1000
        return await client.chat_completion(
            model=context.guardrail.llm_config.model,
            messages=messages,
            timeout_s=timeout_s,
            temperature=0.0,
        )

    def _parse_response(
        self, config: ContextAwareConfig, response_text: str
    ) -> Dict[str, Any]:
        """Parse the model response into a normalized dict."""

        obj = _safe_json_loads(response_text)
        violation = obj.get(config.output_schema.violation_field, 1)
        confidence = obj.get(config.output_schema.confidence_field, "low")
        policy_category = obj.get(config.output_schema.category_field)
        rationale = obj.get(config.output_schema.rationale_field, "")

        return {
            "violation": int(violation),
            "policy_category": policy_category,
            "confidence": confidence,
            "rationale": str(rationale)[:240],
            "raw": obj,
        }

    def _error_result(
        self,
        start: float,
        context: PolicyContext,
        config: Optional[ContextAwareConfig],
        error_type: str,
        message: str,
    ) -> PolicyResult:
        """Create a PolicyResult for failures according to fail-closed rules."""

        fail_closed = True if config is None else config.fail_closed_on_error
        if fail_closed:
            status = "BLOCK"
            severity = "HIGH"
            context.stop_event.set()
        else:
            status = "ALLOW" if context.guardrail.mode == "MONITOR" else "ERROR"
            severity = "LOW" if status == "ALLOW" else "MEDIUM"
        return PolicyResult(
            policy_id=self.policy_id,
            type=self.type,
            name=self.name,
            status=status,
            severity=severity,
            details={"error": error_type, "message": message},
            latency_ms=(time.perf_counter() - start) * 1000,
        )


def _build_llm_headers(llm_config) -> dict[str, str]:
    """Resolve request headers for the configured LLM endpoint."""

    auth = getattr(llm_config, "auth", None)
    if auth is None:
        api_key = _get_legacy_llm_api_key(llm_config.base_url)
        return {"Authorization": f"Bearer {api_key}"}

    auth_type = str(auth.type).strip().lower()
    if auth_type == "none":
        return {}

    secret_env = (auth.secret_env or "").strip()
    if not secret_env:
        raise RuntimeError("LLM auth requires auth.secret_env to be set")

    load_env()
    secret_value = os.getenv(secret_env)
    if not secret_value:
        raise RuntimeError(f"Missing LLM auth secret. Set {secret_env}.")

    if auth_type == "bearer":
        return {"Authorization": f"Bearer {secret_value}"}
    if auth_type == "header":
        header_name = (auth.header_name or "").strip()
        if not header_name:
            raise RuntimeError("LLM header auth requires auth.header_name")
        return {header_name: secret_value}
    raise RuntimeError(f"Unsupported LLM auth type: {auth.type}")


def _get_legacy_llm_api_key(base_url: str) -> str:
    """Resolve the API key based on the configured LLM endpoint."""

    load_env()
    base = (base_url or "").lower()

    if "groq.com" in base:
        keys = ("GROQ_API_KEY", "LLM_API_KEY")
        message = "Missing Groq API key. Set GROQ_API_KEY (or LLM_API_KEY)."
    elif "openrouter.ai" in base:
        keys = ("OPENROUTER_API_KEY", "LLM_API_KEY")
        message = "Missing OpenRouter API key. Set OPENROUTER_API_KEY (or LLM_API_KEY)."
    elif "openai.com" in base:
        keys = ("OPENAI_API_KEY", "LLM_API_KEY")
        message = "Missing OpenAI API key. Set OPENAI_API_KEY (or LLM_API_KEY)."
    elif "huggingface.co" in base or "hf.co" in base:
        keys = ("HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN", "HUGGINGFACE_API_KEY", "LLM_API_KEY")
        message = (
            "Missing Hugging Face token. Set HF_TOKEN, HUGGINGFACEHUB_API_TOKEN, "
            "HUGGINGFACE_API_KEY, or LLM_API_KEY."
        )
    else:
        keys = ("LLM_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "HF_TOKEN")
        message = (
            "Missing LLM API key. Set LLM_API_KEY (or OPENROUTER_API_KEY / GROQ_API_KEY / OPENAI_API_KEY / HF_TOKEN)."
        )

    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    raise RuntimeError(message)


def _severity_from_confidence(confidence: str) -> str:
    """Map confidence labels to severity buckets."""

    rank = _CONFIDENCE_RANK.get(confidence, 0)
    if rank >= 2:
        return "HIGH"
    if rank == 1:
        return "MEDIUM"
    return "LOW"


def _safe_json_loads(text: str) -> Dict[str, Any]:
    """Parse JSON from an LLM response, tolerating leading/trailing text."""

    cleaned = text.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        last_exc = exc

    start = cleaned.find("{")
    if start == -1:
        snippet = cleaned[:200]
        raise ValueError(
            f"No JSON object found in LLM response: {snippet}"
        ) from last_exc
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(cleaned[start:])
        return obj
    except json.JSONDecodeError as exc:
        snippet = cleaned[start : start + 200]
        raise ValueError(
            f"Failed to parse JSON from LLM response: {snippet}"
        ) from exc
