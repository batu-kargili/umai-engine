from __future__ import annotations

import unittest

from app.core.pipeline import Pipeline
from app.models.guardrail import (
    AgtConfig,
    AgtPolicyCondition,
    AgtPolicyDocument,
    AgtPolicyRule,
    GuardrailSnapshot,
    HeuristicConfig,
    LLMConfig,
    Policy,
)
from app.models.request import ChatMessage, InputArtifact, InputPayload, InternalRequest


class _Store:
    def __init__(self, snapshot: GuardrailSnapshot) -> None:
        self.snapshot = snapshot

    async def get_guardrail(
        self,
        tenant_id: str,
        environment_id: str,
        project_id: str,
        guardrail_id: str,
        version: int,
    ) -> GuardrailSnapshot | None:
        del tenant_id, environment_id, project_id, guardrail_id, version
        return self.snapshot


def _agt_config(mode: str = "ENFORCE") -> AgtConfig:
    return AgtConfig(
        enabled=True,
        mode=mode,  # type: ignore[arg-type]
        enforced_phases=["TOOL_INPUT", "MCP_REQUEST", "MEMORY_WRITE"],
        bundle_ref="umai-agt-action-baseline/v1",
        fail_closed=True,
        policy_document=AgtPolicyDocument(
            version="1",
            default_action="ALLOW",
            rules=[
                AgtPolicyRule(
                    id="read-only-allow",
                    effect="ALLOW",
                    severity="LOW",
                    conditions=[
                        AgtPolicyCondition(
                            field="action_family",
                            operator="EQUALS",
                            value="read_only",
                        )
                    ],
                ),
                AgtPolicyRule(
                    id="dangerous-mcp-block",
                    effect="BLOCK",
                    severity="HIGH",
                    conditions=[
                        AgtPolicyCondition(field="phase", operator="EQUALS", value="MCP_REQUEST"),
                        AgtPolicyCondition(
                            field="method",
                            operator="IN",
                            value=["delete", "remove", "exec"],
                        ),
                    ],
                ),
                AgtPolicyRule(
                    id="write-step-up",
                    effect="STEP_UP_APPROVAL",
                    severity="HIGH",
                    conditions=[
                        AgtPolicyCondition(field="action", operator="IN", value=["write", "delete"])
                    ],
                ),
            ],
        ),
    )


def _snapshot(
    *,
    agt_mode: str = "ENFORCE",
    policies: list[Policy] | None = None,
) -> GuardrailSnapshot:
    return GuardrailSnapshot(
        guardrail_id="gr-agt",
        version=1,
        mode="ENFORCE",
        phases=["TOOL_INPUT", "MCP_REQUEST", "MEMORY_WRITE"],
        preflight=HeuristicConfig(target="LAST_MESSAGE", rules=[], max_length=8000),
        policies=policies or [],
        llm_config=LLMConfig(
            provider="OPENAI",
            base_url="https://example.invalid/v1",
            model="gpt-test",
            timeout_ms=1000,
        ),
        agt=_agt_config(agt_mode),
    )


def _request(
    *,
    phase: str,
    content: str,
    artifact: InputArtifact | None,
) -> InternalRequest:
    return InternalRequest(
        request_id="req-1",
        timestamp="2026-04-21T12:00:00Z",
        tenant_id="tenant-1",
        environment_id="env-1",
        project_id="proj-1",
        guardrail_id="gr-agt",
        guardrail_version=1,
        phase=phase,  # type: ignore[arg-type]
        input=InputPayload(
            messages=[ChatMessage(role="user", content=content)],
            phase_focus="LAST_USER_MESSAGE",
            content_type="text",
            artifacts=[artifact] if artifact else [],
        ),
        timeout_ms=1000,
    )


class AgtPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_input_read_only_allows(self) -> None:
        pipeline = Pipeline(_Store(_snapshot()))
        request = _request(
            phase="TOOL_INPUT",
            content="Look up the latest account status.",
            artifact=InputArtifact(
                artifact_type="TOOL_INPUT",
                name="project.lookup",
                payload_summary="Read account status",
                metadata={
                    "agent_id": "agent-1",
                    "action": "read",
                    "tool_name": "project.lookup",
                },
            ),
        )

        response = await pipeline.evaluate(request)

        self.assertEqual(response.decision.action, "ALLOW")
        self.assertIsNone(response.triggering_policy)

    async def test_tool_input_write_requires_step_up(self) -> None:
        pipeline = Pipeline(_Store(_snapshot()))
        request = _request(
            phase="TOOL_INPUT",
            content="Update the CRM record.",
            artifact=InputArtifact(
                artifact_type="TOOL_INPUT",
                name="crm.update",
                payload_summary="Update CRM record",
                metadata={
                    "agent_id": "agent-1",
                    "action": "write",
                    "tool_name": "crm.update",
                },
            ),
        )

        response = await pipeline.evaluate(request)

        self.assertEqual(response.decision.action, "STEP_UP_APPROVAL")
        self.assertEqual(response.triggering_policy.details.get("policy_source"), "agt")
        self.assertEqual(response.triggering_policy.details.get("matched_rule_id"), "write-step-up")

    async def test_mcp_request_dangerous_method_blocks(self) -> None:
        pipeline = Pipeline(_Store(_snapshot()))
        request = _request(
            phase="MCP_REQUEST",
            content="Delete the connector workspace.",
            artifact=InputArtifact(
                artifact_type="MCP_REQUEST",
                name="project-mcp",
                payload_summary="Delete workspace",
                metadata={
                    "agent_id": "agent-1",
                    "action": "delete",
                    "server_name": "project-mcp",
                    "method": "delete",
                },
            ),
        )

        response = await pipeline.evaluate(request)

        self.assertEqual(response.decision.action, "BLOCK")
        self.assertEqual(response.triggering_policy.details.get("matched_rule_id"), "dangerous-mcp-block")

    async def test_mcp_block_outweighs_read_only_allow_when_both_match(self) -> None:
        pipeline = Pipeline(_Store(_snapshot()))
        request = _request(
            phase="MCP_REQUEST",
            content="Run a destructive MCP method.",
            artifact=InputArtifact(
                artifact_type="MCP_REQUEST",
                name="project-mcp",
                payload_summary="Delete workspace through MCP",
                metadata={
                    "agent_id": "agent-1",
                    "action": "read",
                    "server_name": "project-mcp",
                    "method": "delete",
                },
            ),
        )

        response = await pipeline.evaluate(request)

        self.assertEqual(response.decision.action, "BLOCK")
        self.assertEqual(response.triggering_policy.details.get("matched_rule_id"), "dangerous-mcp-block")

    async def test_missing_action_metadata_blocks_in_enforce_mode(self) -> None:
        pipeline = Pipeline(_Store(_snapshot()))
        request = _request(
            phase="TOOL_INPUT",
            content="Export the customer list.",
            artifact=InputArtifact(
                artifact_type="TOOL_INPUT",
                name="crm.export",
                payload_summary="Export customer list",
                metadata={"agent_id": "agent-1", "action": "export"},
            ),
        )

        response = await pipeline.evaluate(request)

        self.assertEqual(response.decision.action, "BLOCK")
        self.assertEqual(response.triggering_policy.details.get("error"), "missing_action_metadata")

    async def test_advisory_mode_downgrades_missing_metadata_to_warning(self) -> None:
        pipeline = Pipeline(_Store(_snapshot(agt_mode="ADVISORY")))
        request = _request(
            phase="TOOL_INPUT",
            content="Export the customer list.",
            artifact=InputArtifact(
                artifact_type="TOOL_INPUT",
                name="crm.export",
                payload_summary="Export customer list",
                metadata={"agent_id": "agent-1", "action": "export"},
            ),
        )

        response = await pipeline.evaluate(request)

        self.assertEqual(response.decision.action, "ALLOW_WITH_WARNINGS")
        self.assertEqual(response.triggering_policy.type, "AGT")

    async def test_existing_policy_block_outweighs_agt_step_up(self) -> None:
        heuristic_policy = Policy(
            id="pol-delete-keyword",
            type="HEURISTIC",
            name="Delete Keyword Block",
            enabled=True,
            phases=["TOOL_INPUT"],
            config={
                "target": "LAST_MESSAGE",
                "rules": [
                    {
                        "id": "delete-keyword",
                        "mode": "EXACT",
                        "pattern": "Delete the workspace.",
                        "block_on_match": True,
                    }
                ],
                "max_length": 1000,
            },
        )
        pipeline = Pipeline(_Store(_snapshot(policies=[heuristic_policy])))
        request = _request(
            phase="TOOL_INPUT",
            content="Delete the workspace.",
            artifact=InputArtifact(
                artifact_type="TOOL_INPUT",
                name="workspace.delete",
                payload_summary="Delete workspace",
                metadata={
                    "agent_id": "agent-1",
                    "action": "write",
                    "tool_name": "workspace.delete",
                },
            ),
        )

        response = await pipeline.evaluate(request)

        self.assertEqual(response.decision.action, "BLOCK")
        self.assertEqual(response.triggering_policy.policy_id, "pol-delete-keyword")


if __name__ == "__main__":
    unittest.main()
