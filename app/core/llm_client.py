"""HTTP client wrapper for OpenAI-compatible chat completions."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

# Transient upstream conditions worth retrying. 429 in particular is what
# OpenRouter returns when a model is "temporarily rate-limited upstream"
# ("Please retry shortly"); 502/503/529 are transient provider hiccups.
_RETRYABLE_STATUS = {429, 502, 503, 529}
_MAX_ATTEMPTS = 3
_MAX_BACKOFF_S = 2.0


class LLMClient:
    """Minimal async client for OpenAI-compatible chat completions."""

    def __init__(self, base_url: str, headers: dict[str, str] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {})

    async def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        timeout_s: float,
        temperature: float = 0.0,
    ) -> str:
        """Submit a chat completion request and return the raw content.

        Retries a small number of times with backoff on transient upstream
        rate-limit / availability errors (HTTP 429/502/503/529), since the
        provider explicitly asks callers to retry shortly.
        """

        url = f"{self.base_url}/chat/completions"
        payload = {"model": model, "messages": messages, "temperature": temperature}

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            last_status: int | None = None
            for attempt in range(_MAX_ATTEMPTS):
                resp = await client.post(url, json=payload, headers=self.headers)
                if (
                    resp.status_code in _RETRYABLE_STATUS
                    and attempt < _MAX_ATTEMPTS - 1
                ):
                    last_status = resp.status_code
                    delay = self._retry_delay(resp, attempt)
                    logger.warning(
                        "llm.retry status=%s attempt=%s delay=%.2fs model=%s",
                        resp.status_code,
                        attempt + 1,
                        delay,
                        model,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]

        # Exhausted retries on a retryable status; surface a clear error.
        raise httpx.HTTPStatusError(
            f"LLM upstream rate-limited after {_MAX_ATTEMPTS} attempts (status {last_status})",
            request=httpx.Request("POST", url),
            response=httpx.Response(last_status or 429),
        )

    @staticmethod
    def _retry_delay(resp: httpx.Response, attempt: int) -> float:
        """Honor Retry-After when present, otherwise exponential backoff+jitter."""

        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_S)
            except ValueError:
                pass
        return min(0.4 * (2**attempt) + random.uniform(0.0, 0.2), _MAX_BACKOFF_S)
