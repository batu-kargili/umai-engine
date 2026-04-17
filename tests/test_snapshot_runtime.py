from __future__ import annotations

import hashlib
import hmac
import json
import os
import unittest
from contextlib import contextmanager
from typing import Iterator

from app.core import guardrail_store
from app.core.guardrail_store import DummyGuardrailStore, RedisGuardrailStore, get_guardrail_store
from app.core.snapshot_verifier import verify_snapshot_signature


@contextmanager
def patched_environ(**updates: str | None) -> Iterator[None]:
    original = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def reset_store() -> Iterator[None]:
    original = guardrail_store._STORE
    guardrail_store._STORE = None
    try:
        yield
    finally:
        guardrail_store._STORE = original


class SnapshotVerifierTests(unittest.TestCase):
    def test_accepts_matching_hex_hmac_signature(self) -> None:
        snapshot = {"guardrail_id": "gr-1", "version": 1, "mode": "ENFORCE"}
        message = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(b"secret-key", message, hashlib.sha256).hexdigest()
        with patched_environ(SNAPSHOT_SIGNING_KEY="secret-key"):
            self.assertTrue(verify_snapshot_signature(snapshot, signature, "default"))

    def test_rejects_missing_signature_when_verification_enabled(self) -> None:
        snapshot = {"guardrail_id": "gr-1", "version": 1, "mode": "ENFORCE"}
        with patched_environ(SNAPSHOT_SIGNING_KEY="secret-key"):
            self.assertFalse(verify_snapshot_signature(snapshot, None, "default"))

    def test_accepts_unsigned_snapshot_when_verification_disabled(self) -> None:
        snapshot = {"guardrail_id": "gr-1", "version": 1, "mode": "ENFORCE"}
        with patched_environ(SNAPSHOT_SIGNING_KEY=None):
            self.assertTrue(verify_snapshot_signature(snapshot, None, None))


class GuardrailStoreSelectionTests(unittest.TestCase):
    def test_uses_dummy_store_without_redis_in_dev_mode(self) -> None:
        with reset_store(), patched_environ(REDIS_URL=None, REQUIRE_REDIS=None):
            store = get_guardrail_store()
        self.assertIsInstance(store, DummyGuardrailStore)

    def test_uses_redis_store_when_configured(self) -> None:
        with reset_store(), patched_environ(REDIS_URL="redis://redis:6379/0", REQUIRE_REDIS=None):
            store = get_guardrail_store()
        self.assertIsInstance(store, RedisGuardrailStore)

    def test_requires_redis_when_explicitly_enabled(self) -> None:
        with reset_store(), patched_environ(REDIS_URL=None, REQUIRE_REDIS="true"):
            with self.assertRaisesRegex(RuntimeError, "REQUIRE_REDIS=true"):
                get_guardrail_store()


if __name__ == "__main__":
    unittest.main()
