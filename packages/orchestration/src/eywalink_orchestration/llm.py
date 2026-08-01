"""LLM client for local model serving (SGLang / vLLM / Ollama).

Uses the OpenAI-compatible chat completions API over httpx so it works with
any local server exposing ``/v1/chat/completions`` — zero lock-in.

Timeout rationale:
- read=600s: local LLMs on shared VRAM can take minutes per completion
  (speculative decoding, long generations).
- connect=30s: generous but bounded startup/connection window.
- pool limits keep concurrency sane on a single-GPU host (VRAM contention).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(
    connect=30.0,
    read=600.0,
    write=120.0,
    pool=30.0,
)


class LLMClient:
    """Thin OpenAI-compatible chat client with resilient timeouts."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "not-needed",
        timeout: httpx.Timeout | None = None,
        max_concurrent: int = 4,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout or DEFAULT_TIMEOUT,
            limits=httpx.Limits(
                max_connections=max_concurrent,
                max_keepalive_connections=max_concurrent,
            ),
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, messages: list[dict[str, str]], **overrides: Any) -> str:
        """Send a chat completion and return the assistant text."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": overrides.pop("temperature", self.temperature),
            "max_tokens": overrides.pop("max_tokens", self.max_tokens),
            "stream": False,
            **overrides,
        }
        try:
            resp = await self._client.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"LLM request timed out after {DEFAULT_TIMEOUT.read}s "
                f"(model={self.model}, base={self.base_url})"
            ) from exc
        except httpx.HTTPStatusError as exc:
            logger.error(
                "LLM HTTP %s: %s", exc.response.status_code, exc.response.text[:500]
            )
            raise LLMError(
                f"LLM request failed with HTTP {exc.response.status_code}"
            ) from exc

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response shape: {data}") from exc

    async def complete_message(
        self, messages: list[dict[str, Any]], **overrides: Any
    ) -> dict[str, Any]:
        """Send a chat completion and return the full assistant message dict.

        Unlike :meth:`complete` (which returns just the text), this returns
        the raw message object so callers can inspect ``tool_calls`` for the
        tool-use loop in :mod:`eywalink_orchestration.mcp`.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": overrides.pop("temperature", self.temperature),
            "max_tokens": overrides.pop("max_tokens", self.max_tokens),
            "stream": False,
            **overrides,
        }
        try:
            resp = await self._client.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"LLM request timed out after {DEFAULT_TIMEOUT.read}s "
                f"(model={self.model}, base={self.base_url})"
            ) from exc
        except httpx.HTTPStatusError as exc:
            logger.error(
                "LLM HTTP %s: %s", exc.response.status_code, exc.response.text[:500]
            )
            raise LLMError(
                f"LLM request failed with HTTP {exc.response.status_code}"
            ) from exc

        data = resp.json()
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response shape: {data}") from exc

    async def complete_json(
        self, messages: list[dict[str, str]], **overrides: Any
    ) -> dict[str, Any]:
        """Request a JSON object response (server-side JSON mode).

        The raw text is parsed leniently: first try ``json.loads`` on the raw
        content, then fall back to extracting the first JSON object embedded
        in fenced code blocks (`````json ... `````).
        """
        import json

        raw = await self.complete(
            messages, response_format={"type": "json_object"}, **overrides
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Strip markdown fences if the server ignored JSON mode.
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end > start:
                data = json.loads(raw[start : end + 1])
            else:
                raise LLMError(f"LLM JSON response was not parseable: {raw[:500]}") from None
        if isinstance(data, dict) and "error" in data:
            # Agent contract: an LLM response shaped {"error": ...} means the
            # model rejected the task; surface it as an LLMError so the node
            # runner marks the run failed instead of continuing on garbage.
            raise LLMError(f"LLM returned error: {data['error']}")
        return data


class LLMError(RuntimeError):
    """Base error for LLM client failures."""


class LLMTimeoutError(LLMError):
    """Raised when the LLM request exceeds the read timeout."""
