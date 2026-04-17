"""HMAC-SHA256 signature verification for guardrail snapshots."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("umai.engine.store.verifier")


def verify_snapshot_signature(
    snapshot: Dict[str, Any],
    signature: Optional[str],
    key_id: Optional[str],
) -> bool:
    """Verify the HMAC-SHA256 signature of a guardrail snapshot.

    If SNAPSHOT_SIGNING_KEY is not configured, all snapshots pass (dev/OSS mode).
    If it is configured, snapshots without a matching signature are rejected.

    The signature is computed over a canonical JSON representation of the snapshot
    payload (keys sorted, no whitespace) using HMAC-SHA256.  The service side must
    produce signatures using the same algorithm and the same shared secret.
    """
    signing_key = os.getenv("SNAPSHOT_SIGNING_KEY", "").strip()

    if not signing_key:
        logger.debug("snapshot_verifier.signing_disabled: accepting snapshot without verification")
        return True

    if not signature:
        logger.warning(
            "snapshot_verifier.missing_signature key_id=%s: "
            "SNAPSHOT_SIGNING_KEY is set but snapshot carries no signature",
            key_id,
        )
        return False

    # Exclude envelope-only fields before computing the digest
    payload = {k: v for k, v in snapshot.items() if k not in ("signature", "key_id")}
    message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected = hmac.new(signing_key.encode("utf-8"), message, hashlib.sha256).hexdigest()

    if hmac.compare_digest(expected, signature):
        logger.debug("snapshot_verifier.ok key_id=%s", key_id)
        return True

    logger.warning("snapshot_verifier.invalid_signature key_id=%s", key_id)
    return False