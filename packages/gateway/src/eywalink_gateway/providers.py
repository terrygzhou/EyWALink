"""OpenAI-compatible provider clients with per-provider circuit breakers.

Each provider talks the OpenAI /v1/chat/completions protocol (SGLang,
vLLM, Ollama and most self-hosted servers implement it), which keeps the
gateway provider-agnostic — add a new backend by pointing it at any
OpenAI-compatible endpoint. Zero lock-in.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx

from .config import ProviderSettings
from .metrics import PROVIDER_CIRCUIT_OPEN, PROVIDER_UP

# A provider is tripped after this many consecutive failures.
CIRCUIT_FAILURE_THRESHOLD = 3
# And stays open for this long before a probe is allowed.
CIRCUIT_COOLDOWN_SECONDS = 30.0


class ProviderError(Exception):
    """Raised when a provider call fails and another may be tried."""


@dataclass
class CircuitBreaker:
    """Simple per-provider circuit breaker."""

    failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD
    cooldown: float = CIRCUIT_COOLDOWN_SECONDS
    failures: int = 0
    opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        return (time.monotonic() - self.opened_at) < self.cooldown

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()

    def export_state(self) -> tuple[bool, int]:
        """Return (circuit_open, consecutive_failures)."""
        return self.is_open, self.failures


@dataclass
class Provider:
    """A single OpenAI-compatible model backend."""

    name: str
    settings: ProviderSettings
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    def _base(self) -> str:
        return self.settings.url.rstrip("/")

    async def chat(
        self,
        client: httpx.AsyncClient,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict:
        """Call /v1/chat/completions. Raises ProviderError on any failure."""
        url = f"{self._base()}/v1/chat/completions"
        payload: dict = {
            "model": model or self.settings.model,
            "messages": messages,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            resp = await client.post(
                url,
                json=payload,
                timeout=self.settings.timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: transport error: {exc}") from exc

        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}"
            )

        return resp.json()

    def mark_success(self) -> None:
        self.breaker.record_success()
        PROVIDER_UP.labels(provider=self.name).set(1)
        PROVIDER_CIRCUIT_OPEN.labels(provider=self.name).set(0)

    def mark_failure(self) -> None:
        self.breaker.record_failure()
        PROVIDER_UP.labels(provider=self.name).set(0)
        PROVIDER_CIRCUIT_OPEN.labels(provider=self.name).set(
            1 if self.breaker.is_open else 0
        )


class ProviderPool:
    """Holds all configured providers in fallback order."""

    def __init__(self, ordered: list[tuple[str, ProviderSettings]]):
        self.providers = [
            Provider(name=name, settings=settings) for name, settings in ordered
        ]

    def available(self) -> list[Provider]:
        return [p for p in self.providers if not p.breaker.is_open]

    def by_name(self, name: str) -> Provider | None:
        return next((p for p in self.providers if p.name == name), None)

    def health(self) -> dict[str, dict]:
        return {
            p.name: {
                "circuit_open": p.breaker.is_open,
                "consecutive_failures": p.breaker.failures,
                "model": p.settings.model,
                "url": p.settings.url,
            }
            for p in self.providers
        }


async def shutdown_probe(provider: Provider, client: httpx.AsyncClient) -> bool:
    """Half-open probe: after cooldown, try a lightweight call."""
    try:
        resp = await client.get(f"{provider._base()}/health", timeout=5.0)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False
