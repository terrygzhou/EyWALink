"""LLM client for OpenAI-compatible endpoints (vLLM, SGLang, llama.cpp).

CRITICAL: httpx's default READ timeout is 5s and silently kills long local
LLM generations even when `timeout` is raised on the SDK layer. Always
construct the client with an explicit httpx.Timeout (read >= 600s for local
27B+ models). See agent-pipeline-orchestration skill section 4.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(
    connect=30.0,
    read=600.0,  # local models are slow; never use the 5s httpx default
    write=60.0,
    pool=30.0,
)


class LLMError(RuntimeError):
    """Raised when the LLM endpoint fails after retries."""


class LLMClient:
    """Minimal OpenAI-compatible chat client backed by httpx.

    No SDK dependency — works with any /v1/chat/completions endpoint.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        max_tokens: int = 32768,
        temperature: float = 0.7,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        # The httpx timeout fix — pass through explicitly.
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )

    # ------------------------------------------------------------------ #
    # factories
    # ------------------------------------------------------------------ #
    @classmethod
    def from_config(cls, cfg: dict | None = None) -> "LLMClient":
        """Build from config dict with env-var fallbacks.

        Precedence: cfg dict -> env (LLM_BASE_URL/LLM_MODEL/LLM_API_KEY) -> defaults.
        """
        cfg = cfg or {}
        base_url = (
            cfg.get("base_url")
            or os.environ.get("LLM_BASE_URL")
            or "http://localhost:8080/v1"
        )
        model = (
            cfg.get("model")
            or os.environ.get("LLM_MODEL")
            or "Qwen3.6-35B-A3B"
        )
        return cls(
            base_url=base_url,
            model=model,
            api_key=cfg.get("api_key") or os.environ.get("LLM_API_KEY", ""),
            max_tokens=int(cfg.get("max_tokens", 32768)),
            temperature=float(cfg.get("temperature", 0.7)),
            timeout=httpx.Timeout(
                connect=float(cfg.get("connect_timeout", 30)),
                read=float(cfg.get("read_timeout", 600)),
                write=float(cfg.get("write_timeout", 60)),
                pool=float(cfg.get("pool_timeout", 30)),
            ),
            max_retries=int(cfg.get("max_retries", 2)),
        )

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: str | None = None,
    ) -> str:
        """Send a chat request and return the assistant message content."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.base_url}/chat/completions"
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"].get("content")
                if content is None:
                    # vLLM may return null content on JSON-format requests
                    # when reasoning is still in flight or output is empty.
                    raise ValueError("LLM returned null content")
                return content
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last_err = exc
                logger.warning(
                    "LLM call attempt %d/%d failed: %s", attempt + 1, self.max_retries + 1, exc
                )
        raise LLMError(f"LLM request failed after retries: {last_err}") from last_err

    def chat_text(self, prompt: str, system: str = "", **kwargs: Any) -> str:
        """Convenience: plain-text prompt."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, **kwargs)

    def chat_json(self, prompt: str, system: str = "", **kwargs: Any) -> dict:
        """Convenience: prompt for a JSON object, parse with 3-stage fallback."""
        raw = self.chat(
            [
                {"role": "system", "content": system or "You output only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format="json",
            **kwargs,
        )
        return _extract_json(raw)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _extract_json(text: str | None) -> dict:
    """3-stage JSON extraction: direct -> strip fences -> bracket match."""
    if not text:
        logger.warning("LLM returned empty content for JSON extraction")
        return {}
    text = text.strip()
    # Stage 1: direct parse
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    # Stage 2: strip ```json ... ``` fences
    if text.startswith("```"):
        cleaned = text.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
        try:
            value = json.loads(cleaned)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    # Stage 3: bracket matching — try every '{' position with a matching
    # close so wrapped/nested brace layers (common with reasoning models)
    # still parse.
    for idx in [i for i, ch in enumerate(text) if ch == "{"]:
        end = _matching_brace(text, idx)
        if end is None:
            continue
        try:
            value = json.loads(text[idx : end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    logger.warning("Failed to parse JSON from LLM output (%.200s...)", text)
    return {}


def _matching_brace(text: str, start: int) -> int | None:
    """Return index of the brace matching text[start] ('{' only)."""
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None
