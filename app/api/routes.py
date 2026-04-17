"""API routes for the AI Engine internal service."""

import logging

from fastapi import APIRouter

from app.core.guardrail_store import get_guardrail_store
from app.core.pipeline import Pipeline
from app.models.request import InternalRequest
from app.models.response import InternalResponse

router = APIRouter()
pipeline = Pipeline(get_guardrail_store())
logger = logging.getLogger("umai.engine.api")


@router.get("/healthz")
async def healthz():
    """Health probe for liveness and readiness checks.

    Returns:
        dict: Simple status payload used by orchestrators.
    """

    return {"status": "ok"}


@router.post("/internal/ai-engine/v1/evaluate", response_model=InternalResponse)
async def evaluate(req: InternalRequest) -> InternalResponse:
    """Evaluate a request against guardrail policies.

    Args:
        req: InternalRequest containing normalized input and guardrail context.

    Returns:
        InternalResponse: Normalized guardrail decision output.
    """

    logger.info(
        "engine.evaluate.start request_id=%s tenant_id=%s guardrail_id=%s version=%s phase=%s",
        req.request_id,
        req.tenant_id,
        req.guardrail_id,
        req.guardrail_version,
        req.phase,
    )
    response = await pipeline.evaluate(req)
    logger.info(
        "engine.evaluate.end request_id=%s action=%s allowed=%s severity=%s",
        response.request_id,
        response.decision.action,
        response.decision.allowed,
        response.decision.severity,
    )
    return response
