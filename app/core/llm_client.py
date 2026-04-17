"""HTTP client wrapper for OpenAI-compatible chat completions."""

from __future__ import annotations

from typing import Any, Dict, List

import httpx


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
        """Submit a chat completion request and return the raw content."""

        url = f"{self.base_url}/chat/completions"
        payload = {"model": model, "messages": messages, "temperature": temperature}
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]
