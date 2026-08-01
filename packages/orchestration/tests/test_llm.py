"""Tests for the LLM client with a mocked httpx transport."""

from __future__ import annotations

import httpx
import pytest

from eywalink_orchestration.llm import LLMClient, LLMError, LLMTimeoutError


def _handler(json_body: dict, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=json_body, request=request)

    return handler


@pytest.mark.asyncio
async def test_complete_returns_content() -> None:
    client = LLMClient(
        "http://llm:8080",
        "test-model",
        transport=httpx.MockTransport(
            _handler({"choices": [{"message": {"content": "hello world"}}]})
        ),
    )
    try:
        out = await client.complete([{"role": "user", "content": "hi"}])
        assert out == "hello world"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_complete_json_parses_object() -> None:
    client = LLMClient(
        "http://llm:8080",
        "test-model",
        transport=httpx.MockTransport(
            _handler(
                {
                    "choices": [
                        {"message": {"content": '{"phase": "done", "ok": true}'}}
                    ]
                }
            )
        ),
    )
    try:
        out = await client.complete_json([{"role": "user", "content": "go"}])
        assert out == {"phase": "done", "ok": True}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_complete_json_strips_markdown_fences() -> None:
    client = LLMClient(
        "http://llm:8080",
        "test-model",
        transport=httpx.MockTransport(
            _handler(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '```json\n{"a": 1}\n```'
                            }
                        }
                    ]
                }
            )
        ),
    )
    try:
        out = await client.complete_json([{"role": "user", "content": "go"}])
        assert out == {"a": 1}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_http_error_raises_llm_error() -> None:
    client = LLMClient(
        "http://llm:8080",
        "test-model",
        transport=httpx.MockTransport(_handler({"error": "boom"}, status=500)),
    )
    try:
        with pytest.raises(LLMError):
            await client.complete([{"role": "user", "content": "hi"}])
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_timeout_raises_llm_timeout_error() -> None:
    def slow_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    client = LLMClient(
        "http://llm:8080",
        "test-model",
        transport=httpx.MockTransport(slow_handler),
    )
    try:
        with pytest.raises(LLMTimeoutError):
            await client.complete([{"role": "user", "content": "hi"}])
    finally:
        await client.aclose()
