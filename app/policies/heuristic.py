"""Heuristic policy implementation using regex and exact match rules."""

from __future__ import annotations

import re
import time
from typing import Optional

from pydantic import ValidationError

from app.models.guardrail import HeuristicConfig
from app.models.policy_result import PolicyResult
from app.policies.base import PolicyContext, extract_target_text


class HeuristicPolicy:
    """Evaluate heuristic rules for a given input."""

    type = "HEURISTIC"

    def __init__(self, policy_id: str, name: str) -> None:
        self.policy_id = policy_id
        self.name = name

    async def run(
        self,
        input_text: str,
        context: PolicyContext,
        config: dict,
        llm_client: Optional[object] = None,
    ) -> PolicyResult:
        """Evaluate configured regex/exact rules against the target text."""

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
            parsed_config = HeuristicConfig(**config)
        except ValidationError as exc:
            return PolicyResult(
                policy_id=self.policy_id,
                type=self.type,
                name=self.name,
                status="ERROR",
                severity="MEDIUM",
                details={"error": "invalid_config", "message": str(exc)},
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        if not input_text:
            input_text = extract_target_text(context.request.input, parsed_config.target)

        if parsed_config.max_length and len(input_text) > parsed_config.max_length:
            return PolicyResult(
                policy_id=self.policy_id,
                type=self.type,
                name=self.name,
                status="BLOCK",
                severity="HIGH",
                details={
                    "reason": "max_length_exceeded",
                    "length": len(input_text),
                    "max_length": parsed_config.max_length,
                },
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        flagged_details = None
        lower_text = input_text.lower()
        for rule in parsed_config.rules:
            matched = False
            redacted_text = input_text
            compiled_pattern = None
            if rule.mode == "REGEX":
                try:
                    compiled_pattern = re.compile(rule.pattern)
                except re.error as exc:
                    return PolicyResult(
                        policy_id=self.policy_id,
                        type=self.type,
                        name=self.name,
                        status="ERROR",
                        severity="MEDIUM",
                        details={
                            "error": "invalid_regex",
                            "rule_id": rule.id,
                            "message": str(exc),
                        },
                        latency_ms=(time.perf_counter() - start) * 1000,
                    )
                matched = bool(compiled_pattern.search(input_text))
                if matched and rule.redact_on_match:
                    redacted_text = compiled_pattern.sub(rule.replacement, input_text)
            else:
                matched = rule.pattern.lower() in lower_text
                if matched and rule.redact_on_match:
                    redacted_text = re.sub(
                        re.escape(rule.pattern),
                        rule.replacement,
                        input_text,
                        flags=re.IGNORECASE,
                    )

            if matched:
                details = {
                    "matched_rule_id": rule.id,
                    "matched_pattern": rule.pattern,
                    "mode": rule.mode,
                    "block_on_match": rule.block_on_match,
                    "step_up_on_match": rule.step_up_on_match,
                    "redact_on_match": rule.redact_on_match,
                }
                if rule.block_on_match:
                    return PolicyResult(
                        policy_id=self.policy_id,
                        type=self.type,
                        name=self.name,
                        status="BLOCK",
                        severity="HIGH",
                        details=details,
                        latency_ms=(time.perf_counter() - start) * 1000,
                    )
                if rule.step_up_on_match:
                    return PolicyResult(
                        policy_id=self.policy_id,
                        type=self.type,
                        name=self.name,
                        status="STEP_UP_APPROVAL",
                        severity="HIGH",
                        details=details,
                        latency_ms=(time.perf_counter() - start) * 1000,
                    )
                if rule.redact_on_match:
                    details["replacement"] = rule.replacement
                    details["modified_text"] = redacted_text
                    return PolicyResult(
                        policy_id=self.policy_id,
                        type=self.type,
                        name=self.name,
                        status="ALLOW_WITH_MODIFICATIONS",
                        severity="MEDIUM",
                        details=details,
                        latency_ms=(time.perf_counter() - start) * 1000,
                    )
                flagged_details = details

        if flagged_details:
            return PolicyResult(
                policy_id=self.policy_id,
                type=self.type,
                name=self.name,
                status="FLAG",
                severity="MEDIUM",
                details=flagged_details,
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        return PolicyResult(
            policy_id=self.policy_id,
            type=self.type,
            name=self.name,
            status="ALLOW",
            severity="LOW",
            details={"matched": False},
            latency_ms=(time.perf_counter() - start) * 1000,
        )
