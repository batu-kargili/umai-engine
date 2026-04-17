"""Policy pipeline orchestration for the AI Engine."""

from __future__ import annotations

import asyncio
import logging
import time
from threading import Event
from typing import Iterable, List, Optional

from app.core.guardrail_store import GuardrailStore
from app.models.guardrail import GuardrailSnapshot, Policy
from app.models.policy_result import PolicyResult
from app.models.request import InternalRequest
from app.models.response import (
    Decision,
    ErrorInfo,
    InternalResponse,
    LatencyInfo,
    TriggeringPolicyResult,
)
from app.policies.base import PolicyContext, extract_target_text
from app.policies.context_aware import ContextAwarePolicy
from app.policies.heuristic import HeuristicPolicy

_SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
logger = logging.getLogger("umai.engine.pipeline")


class Pipeline:
    """Evaluate guardrail policies for an incoming request."""

    def __init__(self, guardrail_store: GuardrailStore) -> None:
        self.guardrail_store = guardrail_store

    async def evaluate(self, req: InternalRequest) -> InternalResponse:
        """Run preflight checks, evaluate policies, and build response."""

        start = time.perf_counter()
        errors: List[ErrorInfo] = []

        logger.info(
            "pipeline.snapshot.load tenant_id=%s env=%s project=%s guardrail_id=%s version=%s",
            req.tenant_id,
            req.environment_id,
            req.project_id,
            req.guardrail_id,
            req.guardrail_version,
        )
        snapshot = await self.guardrail_store.get_guardrail(
            req.tenant_id,
            req.environment_id,
            req.project_id,
            req.guardrail_id,
            req.guardrail_version,
        )
        if not snapshot:
            logger.warning(
                "pipeline.snapshot.missing tenant_id=%s env=%s project=%s guardrail_id=%s version=%s",
                req.tenant_id,
                req.environment_id,
                req.project_id,
                req.guardrail_id,
                req.guardrail_version,
            )
            decision = Decision(
                action="BLOCK",
                allowed=False,
                severity="HIGH",
                reason="Guardrail configuration missing",
            )
            latency = LatencyInfo(total=(time.perf_counter() - start) * 1000)
            errors.append(ErrorInfo(type="CONFIG_MISSING", source="guardrail_store"))
            return InternalResponse(
                request_id=req.request_id,
                tenant_id=req.tenant_id,
                environment_id=req.environment_id,
                project_id=req.project_id,
                guardrail_id=req.guardrail_id,
                guardrail_version=req.guardrail_version,
                phase=req.phase,
                decision=decision,
                triggering_policy=None,
                latency_ms=latency,
                errors=errors,
            )

        stop_event = Event()
        context = PolicyContext(request=req, guardrail=snapshot, stop_event=stop_event)

        preflight_result, preflight_latency = await self._run_preflight(snapshot, context)
        if preflight_result and preflight_result.status == "BLOCK":
            logger.info(
                "pipeline.preflight.blocked policy_id=%s severity=%s",
                preflight_result.policy_id,
                preflight_result.severity,
            )
            decision = Decision(
                action="BLOCK",
                allowed=False,
                severity=preflight_result.severity,
                reason=_reason_from_policy(preflight_result, prefix="Preflight"),
            )
            latency = LatencyInfo(
                total=(time.perf_counter() - start) * 1000,
                preflight=preflight_latency,
            )
            return InternalResponse(
                request_id=req.request_id,
                tenant_id=req.tenant_id,
                environment_id=req.environment_id,
                project_id=req.project_id,
                guardrail_id=req.guardrail_id,
                guardrail_version=req.guardrail_version,
                phase=req.phase,
                decision=decision,
                triggering_policy=preflight_result.to_triggering_policy(),
                latency_ms=latency,
                errors=errors,
            )

        policy_results: List[PolicyResult] = []
        tasks: List[asyncio.Task[PolicyResult]] = []

        for policy in snapshot.policies:
            if not policy.enabled or req.phase not in policy.phases:
                logger.info(
                    "pipeline.policy.skip policy_id=%s enabled=%s phase=%s",
                    policy.id,
                    policy.enabled,
                    req.phase,
                )
                continue
            if policy.type == "CONTEXT_AWARE" and not req.flags.allow_llm_calls:
                logger.info("pipeline.policy.llm_disabled policy_id=%s", policy.id)
                policy_results.append(
                    PolicyResult(
                        policy_id=policy.id,
                        type=policy.type,
                        name=policy.name,
                        status="ERROR",
                        severity="MEDIUM",
                        details={"error": "llm_disabled"},
                        latency_ms=0.0,
                    )
                )
                errors.append(
                    ErrorInfo(
                        type="LLM_DISABLED",
                        source=policy.id,
                        message="LLM calls disabled via request flags",
                    )
                )
                continue

            handler = _build_handler(policy)
            if not handler:
                logger.warning("pipeline.policy.unsupported policy_id=%s type=%s", policy.id, policy.type)
                policy_results.append(
                    PolicyResult(
                        policy_id=policy.id,
                        type=policy.type,
                        name=policy.name,
                        status="ERROR",
                        severity="MEDIUM",
                        details={"error": "unsupported_policy_type"},
                        latency_ms=0.0,
                    )
                )
                errors.append(
                    ErrorInfo(
                        type="POLICY_UNSUPPORTED",
                        source=policy.id,
                        message=f"Unsupported policy type {policy.type}",
                    )
                )
                continue

            input_text = extract_target_text(
                req.input, policy.config.get("target", "LAST_MESSAGE")
            )
            logger.info(
                "pipeline.policy.start policy_id=%s type=%s",
                policy.id,
                policy.type,
            )
            task = asyncio.create_task(
                self._run_policy(handler, input_text, context, policy, req.timeout_ms)
            )
            tasks.append(task)

        if tasks:
            results, block_result = await self._collect_results(tasks, stop_event)
            policy_results.extend(results)
        else:
            block_result = None

        error_results = [result for result in policy_results if result.status == "ERROR"]
        flag_results = [result for result in policy_results if result.status == "FLAG"]
        step_up_results = [
            result
            for result in policy_results
            if result.status in {"STEP_UP_APPROVAL", "STEP_UP"}
        ]
        modification_results = [
            result
            for result in policy_results
            if result.status == "ALLOW_WITH_MODIFICATIONS"
        ]
        if error_results:
            errors.extend(_errors_from_results(error_results))

        decision, triggering_policy, output_modifications = _build_decision(
            snapshot,
            block_result,
            error_results,
            flag_results,
            step_up_results,
            modification_results,
        )
        logger.info(
            "pipeline.decision action=%s allowed=%s severity=%s",
            decision.action,
            decision.allowed,
            decision.severity,
        )

        latency = LatencyInfo(
            total=(time.perf_counter() - start) * 1000,
            preflight=preflight_latency,
        )
        return InternalResponse(
            request_id=req.request_id,
            tenant_id=req.tenant_id,
            environment_id=req.environment_id,
            project_id=req.project_id,
            guardrail_id=req.guardrail_id,
            guardrail_version=req.guardrail_version,
            phase=req.phase,
            decision=decision,
            triggering_policy=triggering_policy,
            output_modifications=output_modifications,
            latency_ms=latency,
            errors=errors,
        )

    async def _run_preflight(
        self, snapshot: GuardrailSnapshot, context: PolicyContext
    ) -> tuple[Optional[PolicyResult], Optional[float]]:
        """Execute the preflight heuristic checks."""

        preflight_start = time.perf_counter()
        preflight_policy = HeuristicPolicy("preflight", "Preflight Rules")
        input_text = extract_target_text(context.request.input, snapshot.preflight.target)
        result = await preflight_policy.run(
            input_text, context, snapshot.preflight.model_dump()
        )
        latency = (time.perf_counter() - preflight_start) * 1000
        return result, latency

    async def _run_policy(
        self,
        handler: object,
        input_text: str,
        context: PolicyContext,
        policy: Policy,
        timeout_ms: Optional[int],
    ) -> PolicyResult:
        """Run a single policy with optional timeout enforcement."""

        timeout_s = timeout_ms / 1000 if timeout_ms else None
        try:
            if timeout_s:
                return await asyncio.wait_for(
                    handler.run(input_text, context, policy.config), timeout=timeout_s
                )
            return await handler.run(input_text, context, policy.config)
        except asyncio.TimeoutError:
            return PolicyResult(
                policy_id=policy.id,
                type=policy.type,
                name=policy.name,
                status="ERROR",
                severity="HIGH",
                details={"error": "timeout"},
                latency_ms=(timeout_s or 0.0) * 1000,
            )
        except Exception as exc:
            return PolicyResult(
                policy_id=policy.id,
                type=policy.type,
                name=policy.name,
                status="ERROR",
                severity="HIGH",
                details={"error": "exception", "message": str(exc)},
                latency_ms=0.0,
            )

    async def _collect_results(
        self,
        tasks: Iterable[asyncio.Task[PolicyResult]],
        stop_event: Event,
    ) -> tuple[List[PolicyResult], Optional[PolicyResult]]:
        """Collect policy results, short-circuiting on the first BLOCK."""

        pending = set(tasks)
        results: List[PolicyResult] = []
        block_result: Optional[PolicyResult] = None

        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task.cancelled():
                    continue
                result = task.result()
                results.append(result)
                if result.status == "BLOCK" and block_result is None:
                    block_result = result
                    stop_event.set()
                    # Cancel remaining tasks once a blocking decision is found.
                    for pending_task in pending:
                        pending_task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    return results, block_result

        return results, block_result


def _build_handler(policy: Policy) -> Optional[object]:
    """Instantiate the correct policy handler for the given policy."""

    if policy.type == "HEURISTIC":
        return HeuristicPolicy(policy.id, policy.name)
    if policy.type == "CONTEXT_AWARE":
        return ContextAwarePolicy(policy.id, policy.name)
    return None


def _build_decision(
    snapshot: GuardrailSnapshot,
    block_result: Optional[PolicyResult],
    error_results: List[PolicyResult],
    flag_results: List[PolicyResult],
    step_up_results: List[PolicyResult],
    modification_results: List[PolicyResult],
) -> tuple[Decision, Optional[TriggeringPolicyResult], Optional[dict]]:
    """Aggregate policy results into a final decision and trigger."""

    if block_result:
        decision, trigger = _decision_from_result(
            "BLOCK", False, block_result, _reason_from_policy(block_result)
        )
        return decision, trigger, None

    if error_results:
        triggering = _pick_most_severe(error_results)
        if snapshot.mode == "MONITOR":
            decision, trigger = _decision_from_result(
                "ALLOW_WITH_WARNINGS",
                True,
                triggering,
                _reason_from_policy(triggering),
            )
            return decision, trigger, None
        decision, trigger = _decision_from_result(
            "BLOCK", False, triggering, _reason_from_policy(triggering)
        )
        return decision, trigger, None

    if step_up_results:
        triggering = _pick_most_severe(step_up_results)
        decision, trigger = _decision_from_result(
            "STEP_UP_APPROVAL",
            False,
            triggering,
            _reason_from_policy(triggering),
        )
        return decision, trigger, None

    if modification_results:
        triggering = _pick_most_severe(modification_results)
        decision, trigger = _decision_from_result(
            "ALLOW_WITH_MODIFICATIONS",
            True,
            triggering,
            _reason_from_policy(triggering),
        )
        output_modifications = {
            "policy_id": triggering.policy_id,
            "modified_text": triggering.details.get("modified_text"),
            "replacement": triggering.details.get("replacement"),
            "details": triggering.details,
        }
        return decision, trigger, output_modifications

    if flag_results:
        triggering = _pick_most_severe(flag_results)
        decision, trigger = _decision_from_result(
            "ALLOW_WITH_WARNINGS",
            True,
            triggering,
            _reason_from_policy(triggering),
        )
        return decision, trigger, None

    decision = Decision(
        action="ALLOW",
        allowed=True,
        severity="LOW",
        reason="No policy violations detected",
    )
    return decision, None, None


def _decision_from_result(
    action: str, allowed: bool, result: PolicyResult, reason: str
) -> tuple[Decision, Optional[TriggeringPolicyResult]]:
    """Construct a decision and triggering policy from a result."""

    decision = Decision(
        action=action,
        allowed=allowed,
        severity=result.severity,
        reason=reason,
    )
    return decision, result.to_triggering_policy()


def _reason_from_policy(result: PolicyResult, prefix: Optional[str] = None) -> str:
    """Build a short reason string from a policy result."""

    details = result.details or {}
    if result.type == "HEURISTIC" and "matched_rule_id" in details:
        rule_id = details.get("matched_rule_id")
        return f"{prefix or result.type}: rule {rule_id} matched"
    if result.type == "CONTEXT_AWARE" and "policy_category" in details:
        return f"{result.type}: {details.get('policy_category')}"
    if "error" in details:
        return f"{result.type}: {details.get('error')}"
    return f"{result.type}: {result.status}"


def _pick_most_severe(results: List[PolicyResult]) -> PolicyResult:
    """Select the result with the highest severity rank."""

    return max(results, key=lambda res: _SEVERITY_RANK.get(res.severity, 0))


def _errors_from_results(results: Iterable[PolicyResult]) -> List[ErrorInfo]:
    """Convert policy error results into ErrorInfo entries."""

    errors: List[ErrorInfo] = []
    for result in results:
        error_type = "POLICY_ERROR"
        message = None
        if isinstance(result.details, dict):
            error_type = result.details.get("error", error_type)
            message = result.details.get("message")
        errors.append(
            ErrorInfo(
                type=str(error_type),
                source=result.policy_id,
                message=message,
            )
        )
    return errors
