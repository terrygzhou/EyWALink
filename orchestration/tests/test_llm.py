"""Tests for LLM client (httpx timeout fix) using a stubbed transport."""

from __future__ import annotations

import httpx
import pytest

from eywalink_orchestration.llm import LLMClient, LLMError, _extract_json


def _serve(handler):
    """Minimal httpx transport that intercepts requests without a server."""
    return httpx.MockTransport(handler)


def test_llm_client_chat_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "choices": [{"message": {"content": "hello from model"}}],
        }
        return httpx.Response(200, json=body)

    client = LLMClient(
        base_url="http://fake:8080/v1",
        model="test-model",
        timeout=httpx.Timeout(connect=1.0, read=1.0, write=1.0, pool=1.0),
    )
    client._client = httpx.Client(transport=_serve(handler))
    out = client.chat_text("hi")
    assert out == "hello from model"
    client.close()


def test_llm_client_retries_then_raises():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": "boom"})

    client = LLMClient(
        base_url="http://fake:8080/v1",
        model="test-model",
        max_retries=1,
        timeout=httpx.Timeout(connect=1.0, read=1.0, write=1.0, pool=1.0),
    )
    client._client = httpx.Client(transport=_serve(handler))
    with pytest.raises(LLMError):
        client.chat_text("hi")
    assert calls["n"] == 2  # 1 attempt + 1 retry
    client.close()


def test_extract_json_fences_and_brackets():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('prefix {"b": 2} suffix') == {"b": 2}
    assert _extract_json("no json here") == {}
