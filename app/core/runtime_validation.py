"""Startup environment validation for the UMAI Engine."""

from __future__ import annotations

import logging
import os

from app.core.env import load_env

logger = logging.getLogger("umai.engine.startup")


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def validate_engine_runtime() -> None:
    """Validate environment variables and log a startup summary.

    Logs warnings for missing optional-but-recommended variables so that
    operators can spot misconfiguration without crashing the process.
    """
    load_env()

    license_key = os.getenv("LICENSE_KEY", "").strip()
    if license_key:
        logger.info("startup.license_key=present")
    else:
        logger.info("startup.license_key=optional_missing")

    redis_url = os.getenv("REDIS_URL", "").strip()
    require_redis = _env_truthy("REQUIRE_REDIS")
    if redis_url:
        # Truncate to avoid leaking credentials in logs
        preview = (redis_url[:30] + "...") if len(redis_url) > 30 else redis_url
        logger.info("startup.store=redis url=%s", preview)
    elif require_redis:
        raise RuntimeError(
            "REQUIRE_REDIS=true but REDIS_URL is not set - "
            "the engine cannot load published guardrail snapshots"
        )
    else:
        logger.warning(
            "startup.store=dummy: REDIS_URL not set, "
            "guardrail snapshots will be served from the built-in dummy store"
        )

    signing_key = os.getenv("SNAPSHOT_SIGNING_KEY", "").strip()
    if signing_key:
        logger.info("startup.snapshot_signing=enabled")
    else:
        logger.warning(
            "startup.snapshot_signing=disabled: SNAPSHOT_SIGNING_KEY not set, "
            "all snapshots are accepted without signature verification"
        )

    if redis_url and not signing_key:
        logger.warning(
            "startup.snapshot_signing=disabled_with_redis: published snapshots from redis "
            "will not be signature-verified"
        )
