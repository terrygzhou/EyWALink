"""End-to-end smoke test: boot the FastAPI app and hit real routes.

Mock provider endpoints with respx so no GPU/model server is required.
"""

from __future__ import annotations

import os

os.environ["GATEWAY_OTEL_ENDPOINT"] = ""  # disable OTel exporter in tests

import respx
from fastapi.testclient import TestClient
from httpx import Response

from eywalink_gateway.main import app

OK_RESPONSE = {
    "id": "chatcmpl-smoke",
    "created": 1,
    "model": "nvidia/Qwen3.6-27B-NVFP4",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
}


def test_health_endpoint():
    with TestClient(app) as client:
        r = client.get("/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert set(body["providers"]) == {"sglang", "vllm", "ollama"}


def test_models_endpoint():
    with TestClient(app) as client:
        r = client.get("/v1/models")
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["data"]]
        assert len(ids) == 3


def test_chat_completion_through_app():
    with respx.mock:
        respx.post("http://sglang:8080/v1/chat/completions").mock(
            return_value=Response(200, json=OK_RESPONSE)
        )
        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hello"}]},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["provider"] == "sglang"
        assert body["usage"]["completion_tokens"] == 1


def test_metrics_endpoint():
    with TestClient(app) as client:
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "eywalink_requests_total" in r.text


def test_root_endpoint():
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["service"] == "eywalink-gateway"
