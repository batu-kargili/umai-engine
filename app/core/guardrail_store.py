"""Guardrail stores for loading snapshots used by the policy pipeline."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional, Protocol

from redis.asyncio import Redis

from app.core.dummy_data import DUMMY_GUARDRAILS
from app.core.env import load_env
from app.core.snapshot_verifier import verify_snapshot_signature
from app.models.guardrail import GuardrailSnapshot


class GuardrailStore(Protocol):
    async def get_guardrail(
        self,
        tenant_id: str,
        environment_id: str,
        project_id: str,
        guardrail_id: str,
        version: int,
    ) -> Optional[GuardrailSnapshot]:
        ...


class DummyGuardrailStore:
    """Simple store that resolves guardrail snapshots from static data."""

    async def get_guardrail(
        self,
        tenant_id: str,
        environment_id: str,
        project_id: str,
        guardrail_id: str,
        version: int,
    ) -> Optional[GuardrailSnapshot]:
        key = f"{tenant_id}:{environment_id}:{project_id}:{guardrail_id}:{version}"
        data = DUMMY_GUARDRAILS.get(key)
        if not data:
            return None
        return GuardrailSnapshot(**data)


class RedisGuardrailStore:
    """Redis-backed guardrail snapshot store."""

    def __init__(self, redis_url: str) -> None:
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._logger = logging.getLogger("umai.engine.store.redis")

    async def get_guardrail(
        self,
        tenant_id: str,
        environment_id: str,
        project_id: str,
        guardrail_id: str,
        version: int,
    ) -> Optional[GuardrailSnapshot]:
        key = f"guardrail:{tenant_id}:{environment_id}:{project_id}:{guardrail_id}:{version}"
        self._logger.info("store.redis.get key=%s", key)
        raw = await self._redis.get(key)
        if not raw:
            self._logger.warning("store.redis.miss key=%s", key)
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._logger.warning("store.redis.invalid_json key=%s", key)
            return None
        signature = None
        key_id = None
        snapshot_payload = payload
        if isinstance(payload, dict) and "snapshot" in payload:
            snapshot_payload = payload.get("snapshot")
            signature = payload.get("signature")
            key_id = payload.get("key_id")
        if not isinstance(snapshot_payload, dict):
            self._logger.warning("store.redis.invalid_snapshot key=%s", key)
            return None
        if not verify_snapshot_signature(snapshot_payload, signature, key_id):
            self._logger.warning(
                "store.redis.signature_invalid key=%s key_id=%s",
                key,
                key_id,
            )
            return None
        snapshot_payload["signature"] = signature
        snapshot_payload["key_id"] = key_id
        self._logger.info("store.redis.hit key=%s", key)
        return GuardrailSnapshot(**snapshot_payload)


_STORE: GuardrailStore | None = None


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _runtime_environment() -> str:
    for key in ("UMAI_ENVIRONMENT", "APP_ENV", "ENVIRONMENT", "NODE_ENV"):
        raw = os.getenv(key)
        if raw and raw.strip():
            return raw.strip().lower()
    return "development"


def get_guardrail_store() -> GuardrailStore:
    global _STORE
    if _STORE is None:
        load_env()
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            logging.getLogger("umai.engine.store").info("store.selected redis")
            _STORE = RedisGuardrailStore(redis_url)
        elif _runtime_environment() in {"prod", "production"}:
            raise RuntimeError(
                "Production runtime requires REDIS_URL - dummy guardrail snapshots are disabled"
            )
        elif _env_truthy("REQUIRE_REDIS"):
            raise RuntimeError(
                "REQUIRE_REDIS=true but REDIS_URL is not set - "
                "published guardrail snapshots require redis"
            )
        else:
            logging.getLogger("umai.engine.store").info("store.selected dummy")
            _STORE = DummyGuardrailStore()
    return _STORE
