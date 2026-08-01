"""Chat completion routing with automatic fallback.

The router walks the provider chain in configured order. A provider that
fails (transport error, HTTP 4xx/5xx, timeout) is recorded and the next
healthy provider is tried. If all providers fail, a 503 is returned with
per-provider diagnostics so operators can see exactly what happened.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .config import GatewaySettings, get_settings
from .metrics import FALLBACKS_TOTAL, INFLIGHT, LATENCY_SECONDS, REQUESTS_TOTAL, TOKENS_TOTAL
from .providers import ProviderError, ProviderPool, shutdown_probe

router = APIRouter(prefix="/v1", tags=["completions"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, alias="max_completion_tokens")
    stream: bool = False

    model_config = {"populate_by_name": True}


def _extract_usage(data: dict) -> dict:
    usage = data.get("usage") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }


async def _try_provider(
    pool: ProviderPool,
    provider,
    client: httpx.AsyncClient,
    messages: list[dict],
    req: ChatRequest,
    max_tokens: int,
) -> dict:
    """Call one provider; returns raw response dict on success."""
    start = time.monotonic()
    try:
        data = await provider.chat(
            client,
            messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens or max_tokens,
            model=req.model,
        )
        provider.mark_success()
        LATENCY_SECONDS.labels(provider=provider.name).observe(
            time.monotonic() - start
        )
        return data
    except ProviderError as exc:
        provider.mark_failure()
        raise


async def _complete(
    req: ChatRequest,
    pool: ProviderPool,
    client: httpx.AsyncClient,
    settings: GatewaySettings,
) -> dict:
    messages = [m.model_dump() for m in req.messages]
    max_tokens = settings.max_tokens_default
    last_error: ProviderError | None = None
    attempted: list[str] = []
    used_provider: str | None = None

    for provider in pool.available():
        if provider.breaker.is_open:
            continue
        attempted.append(provider.name)
        try:
            data = await _try_provider(pool, provider, client, messages, req, max_tokens)
            used_provider = provider.name
            return _build_response(data, req, used_provider)
        except ProviderError as exc:
            last_error = exc
            FALLBACKS_TOTAL.labels(
                from_provider=provider.name,
                to_provider="next",
                reason="error",
            ).inc()
            continue

    # If nothing worked, probe the first provider to see if the chain recovered.
    if pool.providers:
        first = pool.providers[0]
        if first.breaker.is_open and await shutdown_probe(first, client):
            first.breaker.opened_at = None  # half-open: allow retry
            first.breaker.failures = 0
            try:
                data = await _try_provider(pool, first, client, messages, req, max_tokens)
                used_provider = first.name
                return _build_response(data, req, used_provider)
            except ProviderError as exc:
                last_error = exc

    raise HTTPException(
        status_code=503,
        detail={
            "error": "all providers failed",
            "attempted": attempted,
            "last_error": str(last_error),
            "health": pool.health(),
        },
    )


def _build_response(data: dict, req: ChatRequest, provider: str) -> dict:
    usage = _extract_usage(data)
    TOKENS_TOTAL.labels(provider=provider, kind="prompt").inc(usage["prompt_tokens"])
    TOKENS_TOTAL.labels(provider=provider, kind="completion").inc(
        usage["completion_tokens"]
    )
    return {
        "id": data.get("id", f"chatcmpl-{provider}"),
        "object": "chat.completion",
        "created": data.get("created", int(time.time())),
        "model": data.get("model", req.model or ""),
        "choices": data.get("choices", []),
        "usage": usage,
        "provider": provider,
    }


@router.post("/chat/completions")
async def chat_completions(req: ChatRequest, request: Request) -> dict:
    if req.stream:
        raise HTTPException(status_code=400, detail="streaming not yet supported")

    settings: GatewaySettings = request.app.state.settings
    pool: ProviderPool = request.app.state.pool
    client: httpx.AsyncClient = request.app.state.client

    INFLIGHT.inc()
    start = time.monotonic()
    try:
        result = await _complete(req, pool, client, settings)
        REQUESTS_TOTAL.labels(provider=result["provider"], status="ok").inc()
        return result
    except HTTPException:
        REQUESTS_TOTAL.labels(provider="none", status="error").inc()
        raise
    finally:
        INFLIGHT.dec()
        LATENCY_SECONDS.labels(provider="gateway").observe(time.monotonic() - start)


@router.get("/models")
async def list_models(request: Request) -> dict:
    pool: ProviderPool = request.app.state.pool
    return {
        "object": "list",
        "data": [
            {
                "id": p.settings.model,
                "object": "model",
                "owned_by": p.name,
                "healthy": not p.breaker.is_open,
            }
            for p in pool.providers
        ],
    }


@router.get("/health")
async def health(request: Request) -> dict:
    pool: ProviderPool = request.app.state.pool
    return {
        "status": "ok",
        "providers": pool.health(),
        "fallback_chain": request.app.state.settings.ordered_provider_names,
    }
