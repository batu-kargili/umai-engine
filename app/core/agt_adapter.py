"""Microsoft AGT integration adapter for UMAI action-governance phases."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.models.guardrail import AgtConfig, AgtPolicyDocument, GuardrailSnapshot
from app.models.policy_result import PolicyResult
from app.models.request import InternalRequest

logger = logging.getLogger("umai.engine.agt")

SUPPORTED_AGT_PHASES = {"TOOL_INPUT", "MCP_REQUEST", "MEMORY_WRITE"}
REQUIRED_METADATA_BY_PHASE = {
    "TOOL_INPUT": ("agent_id", "action", "tool_name"),
    "MCP_REQUEST": ("agent_id", "action", "server_name", "method"),
    "MEMORY_WRITE": ("agent_id", "action", "memory_scope"),
}
HIGH_IMPACT_ACTIONS = {
    "write",
    "delete",
    "destroy",
    "export",
    "send",
    "share",
    "publish",
    "permission-change",
    "permissions-change",
    "rotate-credentials",
}
READ_ONLY_ACTIONS = {"read", "get", "list", "lookup", "search", "fetch", "summarize"}
DANGEROUS_MCP_METHODS = {
    "delete",
    "remove",
    "drop",
    "grant",
    "revoke",
    "exec",
    "execute",
}
POLICY_ID = "agt-action-governance"
POLICY_NAME = "Microsoft AGT Action Governance"
POLICY_TYPE = "AGT"
EFFECT_PRECEDENCE = {
    "ALLOW": 0,
    "ALLOW_WITH_WARNINGS": 1,
    "AUDIT": 1,
    "STEP_UP": 2,
    "STEP_UP_APPROVAL": 2,
    "DENY": 3,
    "BLOCK": 3,
}


class AgtEvaluationError(RuntimeError):
    """Raised when AGT action context cannot be evaluated safely."""


def evaluate_agt_policy(
    snapshot: GuardrailSnapshot,
    req: InternalRequest,
) -> PolicyResult | None:
    """Evaluate AGT policy for supported action phases."""

    agt = snapshot.agt
    if not agt or not agt.enabled or req.phase not in agt.enforced_phases:
        return None

    started_at = time.perf_counter()
    if req.phase not in SUPPORTED_AGT_PHASES:
        return _result_from_status(
            agt=agt,
            status="BLOCK" if agt.fail_closed and agt.mode == "ENFORCE" else "FLAG",
            severity="HIGH",
            latency_ms=_latency_ms(started_at),
            details=_base_details(
                agt,
                matched_rule_id="unsupported-phase",
                action=None,
                extra={
                    "policy_source": "agt",
                    "error": "unsupported_phase",
                    "phase": req.phase,
                },
            ),
        )

    try:
        context = _build_action_context(req)
    except AgtEvaluationError as exc:
        missing = getattr(exc, "args", [None, None])
        return _missing_metadata_result(
            agt,
            req.phase,
            missing_metadata=list(missing[1] or []) if len(missing) > 1 else [],
            message=str(exc),
            latency_ms=_latency_ms(started_at),
        )

    try:
        official_result = _try_official_agt(agt, context)
    except Exception as exc:  # pragma: no cover - defensive wrapper
        logger.warning("agt.official.evaluate_failed error=%s", exc)
        official_result = None

    if official_result is not None:
        official_result.latency_ms = _latency_ms(started_at)
        return official_result

    try:
        document = agt.policy_document or _default_policy_document()
        effect, matched_rule_id, severity = _evaluate_policy_document(document, context)
        return _result_from_effect(
            agt=agt,
            effect=effect,
            severity=severity,
            latency_ms=_latency_ms(started_at),
            details=_base_details(
                agt,
                matched_rule_id=matched_rule_id,
                action=context.get("action"),
                extra=context,
            ),
        )
    except Exception as exc:
        logger.warning("agt.fallback.evaluate_failed error=%s", exc)
        if agt.mode == "ADVISORY" or not agt.fail_closed:
            return _result_from_status(
                agt=agt,
                status="FLAG",
                severity="MEDIUM",
                latency_ms=_latency_ms(started_at),
                details=_base_details(
                    agt,
                    matched_rule_id="evaluation-error",
                    action=context.get("action"),
                    extra={
                        **context,
                        "policy_source": "agt",
                        "error": "evaluation_exception",
                        "message": str(exc),
                    },
                ),
            )
        return _result_from_status(
            agt=agt,
            status="BLOCK",
            severity="HIGH",
            latency_ms=_latency_ms(started_at),
            details=_base_details(
                agt,
                matched_rule_id="evaluation-error",
                action=context.get("action"),
                extra={
                    **context,
                    "policy_source": "agt",
                    "error": "evaluation_exception",
                    "message": str(exc),
                },
            ),
        )


def _build_action_context(req: InternalRequest) -> dict[str, Any]:
    artifact = req.input.artifacts[0] if req.input.artifacts else None
    if artifact is None:
        raise AgtEvaluationError(
            f"AGT action governance requires input.artifacts[0] for phase {req.phase}",
            list(REQUIRED_METADATA_BY_PHASE.get(req.phase, ())),
        )

    agent_context = req.agent_context or {}
    metadata = {**(artifact.metadata or {})}
    for key in (
        "agent_id",
        "agent_did",
        "trust_score",
        "trust_tier",
        "capabilities",
        "public_key_fingerprint",
    ):
        if key not in metadata and agent_context.get(key) is not None:
            metadata[key] = agent_context.get(key)
    required = REQUIRED_METADATA_BY_PHASE.get(req.phase, ())
    missing = [field for field in required if not _has_value(metadata.get(field))]
    if missing:
        raise AgtEvaluationError(
            f"Missing required AGT action metadata for {req.phase}: {', '.join(missing)}",
            missing,
        )

    normalized_action = _normalize_action(metadata.get("action"))
    context = {
        "phase": req.phase,
        "artifact_type": artifact.artifact_type,
        "artifact_name": artifact.name,
        "payload_summary": artifact.payload_summary,
        "agent_id": str(metadata.get("agent_id")),
        "agent_did": metadata.get("agent_did"),
        "trust_score": metadata.get("trust_score"),
        "trust_tier": metadata.get("trust_tier"),
        "capabilities": metadata.get("capabilities") or [],
        "public_key_fingerprint": metadata.get("public_key_fingerprint"),
        "run_id": agent_context.get("run_id"),
        "step_id": agent_context.get("step_id"),
        "action": normalized_action,
        "tool_name": metadata.get("tool_name"),
        "server_name": metadata.get("server_name"),
        "method": metadata.get("method"),
        "memory_scope": metadata.get("memory_scope"),
        "capability": metadata.get("capability"),
        "params": metadata.get("params"),
        "classification": metadata.get("classification"),
        "resource_id": metadata.get("resource_id"),
        "side_effect": metadata.get("side_effect"),
        "metadata": metadata,
        "messages": [message.model_dump() for message in req.input.messages],
    }
    context["action_family"] = _classify_action(normalized_action, context)
    return context


def _classify_action(action: str, context: dict[str, Any]) -> str:
    method = str(context.get("method") or "").strip().lower()
    if method in DANGEROUS_MCP_METHODS:
        return "dangerous_mcp"
    if action in READ_ONLY_ACTIONS:
        return "read_only"
    if action in HIGH_IMPACT_ACTIONS:
        return "high_impact"
    return "custom"


def _normalize_action(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    return text or "unknown"


def _try_official_agt(agt: AgtConfig, context: dict[str, Any]) -> PolicyResult | None:
    """Attempt to evaluate using the official AGT runtime when installed."""

    try:
        from agent_os.policies import PolicyEvaluator  # type: ignore
        from agent_os.policies.schema import PolicyDocument  # type: ignore
    except Exception:
        return None

    payload = _to_official_policy_document(agt.policy_document or _default_policy_document())
    if payload is None:
        return None
    evaluator = PolicyEvaluator(PolicyDocument.model_validate(payload))
    raw_result = evaluator.evaluate(context)
    if raw_result is None:
        return None
    raw_data = raw_result if isinstance(raw_result, dict) else getattr(raw_result, "__dict__", {})
    effect = (
        raw_data.get("effect")
        or raw_data.get("decision")
        or raw_data.get("action")
        or raw_data.get("result")
        or "ALLOW"
    )
    severity = str(raw_data.get("severity") or "MEDIUM").upper()
    matched_rule_id = (
        raw_data.get("matched_rule_id")
        or raw_data.get("rule_id")
        or raw_data.get("matched_rule")
        or "official-agt"
    )
    details = _base_details(
        agt,
        matched_rule_id=str(matched_rule_id),
        action=context.get("action"),
        extra={**context, "policy_source": "agt", "official_runtime": True},
    )
    return _result_from_effect(
        agt=agt,
        effect=str(effect),
        severity=severity if severity in {"LOW", "MEDIUM", "HIGH", "CRITICAL"} else "MEDIUM",
        latency_ms=0.0,
        details=details,
    )


def _evaluate_policy_document(
    document: AgtPolicyDocument,
    context: dict[str, Any],
) -> tuple[str, str, str]:
    best_rule: tuple[str, str, str] | None = None
    best_precedence = -1
    for rule in document.rules:
        if all(_condition_matches(condition.model_dump(mode="python"), context) for condition in rule.conditions):
            precedence = _effect_precedence(rule.effect)
            if precedence > best_precedence:
                best_precedence = precedence
                best_rule = (rule.effect, rule.id, rule.severity)
    if best_rule is not None:
        return best_rule
    return document.default_action, "default", _default_severity(document.default_action)


def _condition_matches(condition: dict[str, Any], context: dict[str, Any]) -> bool:
    operator = str(condition.get("operator") or "EQUALS").upper()
    expected = condition.get("value")
    actual = _get_context_value(context, str(condition.get("field") or ""))

    if operator == "EXISTS":
        return actual is not None
    if operator == "NOT_EXISTS":
        return actual is None
    if operator == "EQUALS":
        return actual == expected
    if operator == "NOT_EQUALS":
        return actual != expected
    if operator == "IN":
        return actual in (expected or [])
    if operator == "NOT_IN":
        return actual not in (expected or [])
    if operator == "CONTAINS":
        if isinstance(actual, str):
            return str(expected) in actual
        if isinstance(actual, (list, tuple, set)):
            return expected in actual
        if isinstance(actual, dict):
            return str(expected) in actual
        return False
    if operator == "MATCHES_REGEX":
        return bool(re.search(str(expected), str(actual or "")))
    if operator == "STARTS_WITH":
        return str(actual or "").startswith(str(expected))
    if operator == "ENDS_WITH":
        return str(actual or "").endswith(str(expected))
    if operator == "GT":
        return float(actual) > float(expected)
    if operator == "GTE":
        return float(actual) >= float(expected)
    if operator == "LT":
        return float(actual) < float(expected)
    if operator == "LTE":
        return float(actual) <= float(expected)
    raise ValueError(f"Unsupported AGT operator: {operator}")


def _get_context_value(context: dict[str, Any], field_path: str) -> Any:
    current: Any = context
    for segment in field_path.split("."):
        if not segment:
            continue
        if isinstance(current, dict):
            current = current.get(segment)
        else:
            return None
    return current


def _missing_metadata_result(
    agt: AgtConfig,
    phase: str,
    missing_metadata: list[str],
    message: str,
    latency_ms: float,
) -> PolicyResult:
    details = _base_details(
        agt,
        matched_rule_id="missing-action-metadata",
        action=None,
        extra={
            "policy_source": "agt",
            "error": "missing_action_metadata",
            "phase": phase,
            "missing_metadata": missing_metadata,
            "message": message,
        },
    )
    if agt.mode == "ADVISORY":
        return _result_from_status(
            agt=agt,
            status="FLAG",
            severity="MEDIUM",
            latency_ms=latency_ms,
            details=details,
        )
    return _result_from_status(
        agt=agt,
        status="BLOCK",
        severity="HIGH",
        latency_ms=latency_ms,
        details=details,
    )


def _result_from_effect(
    agt: AgtConfig,
    effect: str,
    severity: str,
    latency_ms: float,
    details: dict[str, Any],
) -> PolicyResult:
    normalized_effect = str(effect or "ALLOW").upper()
    status_map = {
        "ALLOW": "ALLOW",
        "BLOCK": "BLOCK",
        "DENY": "BLOCK",
        "AUDIT": "FLAG",
        "STEP_UP": "STEP_UP_APPROVAL",
        "STEP_UP_APPROVAL": "STEP_UP_APPROVAL",
        "ALLOW_WITH_WARNINGS": "FLAG",
    }
    status = status_map.get(normalized_effect, "ALLOW")
    if agt.mode == "ADVISORY" and status in {"BLOCK", "STEP_UP_APPROVAL"}:
        status = "FLAG"
    return _result_from_status(
        agt=agt,
        status=status,
        severity=severity,
        latency_ms=latency_ms,
        details=details,
    )


def _result_from_status(
    agt: AgtConfig,
    status: str,
    severity: str,
    latency_ms: float,
    details: dict[str, Any],
) -> PolicyResult:
    details.setdefault("policy_source", "agt")
    details.setdefault("bundle_ref", agt.bundle_ref or "inline")
    details.setdefault("mode", agt.mode)
    details.setdefault("fail_closed", agt.fail_closed)
    if status == "ALLOW":
        severity = "LOW"
    return PolicyResult(
        policy_id=POLICY_ID,
        type=POLICY_TYPE,
        name=POLICY_NAME,
        status=status,
        severity=severity,
        details=details,
        latency_ms=latency_ms,
    )


def _base_details(
    agt: AgtConfig,
    matched_rule_id: str,
    action: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    details = {
        "policy_source": "agt",
        "bundle_ref": agt.bundle_ref or "inline",
        "matched_rule_id": matched_rule_id,
        "action": action,
    }
    if extra:
        details.update(extra)
    return details


def _default_policy_document() -> AgtPolicyDocument:
    return AgtPolicyDocument(
        version="1",
        default_action="ALLOW",
        rules=[],
    )


def _default_severity(effect: str) -> str:
    normalized_effect = str(effect or "ALLOW").upper()
    if normalized_effect == "BLOCK":
        return "HIGH"
    if normalized_effect in {"STEP_UP", "STEP_UP_APPROVAL"}:
        return "HIGH"
    if normalized_effect == "ALLOW_WITH_WARNINGS":
        return "MEDIUM"
    return "LOW"


def _effect_precedence(effect: str) -> int:
    return EFFECT_PRECEDENCE.get(str(effect or "ALLOW").upper(), 0)


def _to_official_policy_document(document: AgtPolicyDocument) -> dict[str, Any] | None:
    defaults_action = _map_effect_to_official_action(document.default_action)
    if defaults_action is None:
        return None

    rules: list[dict[str, Any]] = []
    rule_count = len(document.rules)
    for index, rule in enumerate(document.rules):
        action = _map_effect_to_official_action(rule.effect)
        condition = _to_official_condition(rule.conditions)
        if action is None or condition is None:
            return None
        rules.append(
            {
                "name": rule.description or rule.id,
                "condition": condition,
                "action": action,
                "priority": (_effect_precedence(rule.effect) * 100) + (rule_count - index),
                "message": rule.id,
            }
        )

    return {
        "version": document.version,
        "name": "umai-agt-policy",
        "description": "UMAI-compatible subset of AGT action governance policy",
        "rules": rules,
        "defaults": {"action": defaults_action},
    }


def _to_official_condition(conditions: list[Any]) -> dict[str, Any] | None:
    if len(conditions) != 1:
        return None
    condition = conditions[0]
    operator = _map_operator_to_official(str(condition.operator))
    if operator is None:
        return None
    return {
        "field": condition.field,
        "operator": operator,
        "value": condition.value,
    }


def _map_effect_to_official_action(effect: str) -> str | None:
    effect_map = {
        "ALLOW": "allow",
        "ALLOW_WITH_WARNINGS": "audit",
        "BLOCK": "block",
        "DENY": "deny",
    }
    return effect_map.get(str(effect or "ALLOW").upper())


def _map_operator_to_official(operator: str) -> str | None:
    operator_map = {
        "EQUALS": "eq",
        "NOT_EQUALS": "ne",
        "GT": "gt",
        "GTE": "gte",
        "LT": "lt",
        "LTE": "lte",
        "IN": "in",
        "MATCHES_REGEX": "matches",
        "CONTAINS": "contains",
    }
    return operator_map.get(operator.upper())


def _latency_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True
