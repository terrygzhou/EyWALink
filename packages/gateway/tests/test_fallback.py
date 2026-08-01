"""Tests for the gateway fallback chain.

Uses respx to mock provider HTTP endpoints; no real model servers needed.
"""

from __future__ import annotations

import pytest
import respx
from httpx import AsyncClient, Response

from eywalink_gateway.config import GatewaySettings, ProviderSettings
from eywalink_gateway.providers import Provider, ProviderPool
from eywalink_gateway.router import ChatRequest, _complete

SETTINGS = GatewaySettings(
    fallback_chain="sglang,vllm,ollama",
    sglang_url="http://sglang:8080",
    sglang_model="primary-model",
    vllm_url="http://vllm:8000",
    vllm_model="fallback-1",
    ollama_url="http://ollama:11434",
    ollama_model="fallback-2",
    otel_endpoint="",
    metrics_enabled=False,
)

SGLANG = ProviderSettings(url="http://sglang:8080", model="primary-model")
VLLM = ProviderSettings(url="http://vllm:8000", model="fallback-1")
OLLAMA = ProviderSettings(url="http://ollama:11434", model="fallback-2")

OK_RESPONSE = {
    "id": "chatcmpl-test",
    "created": 1,
    "model": "primary-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
}


def make_pool(names: list[str]) -> ProviderPool:
    settings = {"sglang": SGLANG, "vllm": VLLM, "ollama": OLLAMA}
    return ProviderPool([(name, settings[name]) for name in names])


def chat_request() -> ChatRequest:
    return ChatRequest(
        messages=[{"role": "user", "content": "hello"}],  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_primary_provider_success():
    pool = make_pool(["sglang", "vllm"])
    async with respx.mock:
        respx.post("http://sglang:8080/v1/chat/completions").mock(
            return_value=Response(200, json=OK_RESPONSE)
        )
        async with AsyncClient() as client:
            result = await _complete(chat_request(), pool, client, SETTINGS)
    assert result["provider"] == "sglang"


@pytest.mark.asyncio
async def test_fallback_when_primary_down():
    pool = make_pool(["sglang", "vllm"])
    async with respx.mock:
        respx.post("http://sglang:8080/v1/chat/completions").mock(
            return_value=Response(503, json={"error": "busy"})
        )
        respx.post("http://vllm:8000/v1/chat/completions").mock(
            return_value=Response(200, json=OK_RESPONSE)
        )
        async with AsyncClient() as client:
            result = await _complete(chat_request(), pool, client, SETTINGS)
    assert result["provider"] == "vllm"
    sglang = pool.by_name("sglang")
    assert sglang is not None and sglang.breaker.failures == 1


@pytest.mark.asyncio
async def test_all_providers_fail_raises():
    pool = make_pool(["sglang", "vllm", "ollama"])
    async with respx.mock:
        for url in ("http://sglang:8080", "http://vllm:8000", "http://ollama:11434"):
            respx.post(f"{url}/v1/chat/completions").mock(
                return_value=Response(500, json={"error": "boom"})
            )
        async with AsyncClient() as client:
            with pytest.raises(Exception):
                await _complete(chat_request(), pool, client, SETTINGS)
    assert all(p.breaker.failures == 1 for p in pool.providers)


@pytest.mark.asyncio
async def test_circuit_breaker_trips_after_threshold():
    pool = make_pool(["sglang", "vllm"])
    async with respx.mock:
        for url in ("http://sglang:8080", "http://vllm:8000"):
            respx.post(f"{url}/v1/chat/completions").mock(
                return_value=Response(503, json={"error": "down"})
            )
        async with AsyncClient() as client:
            for _ in range(4):
                try:
                    await _complete(chat_request(), pool, client, SETTINGS)
                except Exception:
                    pass
    sglang = pool.by_name("sglang")
    vllm = pool.by_name("vllm")
    assert sglang is not None and sglang.breaker.is_open
    assert vllm is not None and vllm.breaker.failures == 3
    # Iteration 4 tried nothing: both breakers were open.
    assert all(p.breaker.is_open for p in pool.providers)


@pytest.mark.asyncio
async def test_health_endpoint_shape():
    pool = make_pool(["sglang", "vllm"])
    health = pool.health()
    assert set(health) == {"sglang", "vllm"}
    assert health["sglang"]["model"] == "primary-model"
