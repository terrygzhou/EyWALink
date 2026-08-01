# EyWALink Private AI — Reference Implementation

A production-ready, zero lock-in reference stack for self-hosted enterprise AI.
Everything runs on your hardware, every component is open source, and no vendor
holds your models, vectors, or telemetry.

```
┌─────────────────────────────────────────────────────────────┐
│  Clients (OpenAI-compatible)                                 │
│  ── POST /v1/chat/completions ──►  FastAPI Gateway :8000    │
│                                     │  fallback chain        │
│                                     ├─► SGLang :8080 (GPU)   │
│                                     ├─► vLLM  :8001 (GPU)    │
│                                     └─► Ollama :11434 (CPU)  │
│  ── POST /v1/vectors/search ──►  Qdrant :6333 (vectors)     │
└─────────────────────────────────────────────────────────────┘
        │ traces (OTLP)                  │ metrics
        ▼                                ▼
   OTel Collector :4317            Prometheus :9091
        │ traces                          │
        ▼                                 ▼
   Phoenix :6007                    Grafana :3001
```

## Components

| Component   | Role                                   | Location                  |
| ----------- | -------------------------------------- | ------------------------- |
| `gateway`   | FastAPI app, fallback router, metrics  | `packages/gateway/`       |
| `deploy`    | Compose stack, observability configs   | `packages/deploy/`        |
| SGLang      | Primary LLM serving (GPU)              | compose service `sglang`  |
| vLLM        | Secondary LLM (speculative decoding)   | compose service `vllm`    |
| Ollama      | CPU-friendly last resort               | compose service `ollama`  |
| Qdrant      | Vector database                        | compose service `qdrant`  |
| Prometheus  | Metrics scraping                       | compose service           |
| Grafana     | Dashboards (provisioned)               | compose service           |
| Phoenix     | LLM trace inspection                   | compose service           |
| Loki        | Log aggregation                        | compose service           |

## Quickstart

Requirements: Docker with the NVIDIA Container Toolkit (`nvidia-ctk`), 32GB+
VRAM recommended for the default 27B model, ~20GB disk for model weights.

```bash
cd packages/deploy
cp .env.example .env          # review model names + ports
docker compose up -d          # core stack: gateway, sglang, qdrant, observability
docker compose --profile fallback up -d   # add vLLM + Ollama fallbacks
```

Verify:

```bash
curl http://localhost:8000/v1/health
curl http://localhost:8000/v1/models
curl http://localhost:8000/metrics | head
```

Send a chat request (OpenAI-compatible):

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nvidia/Qwen3.6-27B-NVFP4",
    "messages": [{"role": "user", "content": "Explain zero lock-in AI in one sentence."}]
  }'
```

The gateway walks the chain `sglang → vllm → ollama` and returns the first
healthy provider's response, reporting which provider served the request in
the `provider` field of the response.

## What's inside

- **`packages/gateway/`** — FastAPI app:
  - `src/eywalink_gateway/router.py` — `/v1/chat/completions`, `/v1/models`, `/v1/health`, fallback logic
  - `src/eywalink_gateway/providers.py` — OpenAI-compatible clients + per-provider circuit breakers
  - `src/eywalink_gateway/metrics.py` — Prometheus counters/histograms
  - `src/eywalink_gateway/tracing.py` — OpenTelemetry setup (OTLP → collector → Phoenix)
  - `src/eywalink_gateway/vectors.py` — Qdrant vector search endpoints
  - `tests/test_fallback.py` — fallback and circuit-breaker behavior (respx-mocked)
- **`packages/deploy/`** — `docker-compose.yml`, `.env.example`, Prometheus scrape config, Grafana provisioning (datasource + dashboard), OTel collector config.

## Docs

- [Quickstart](docs/QUICKSTART.md) — same as this README, with troubleshooting
- [Deployment guide](docs/DEPLOYMENT.md) — production hardening, networking, backups
- [GPU & VRAM optimization](docs/VRAM-OPTIMIZATION.md) — right-size models to hardware
- [Multi-model fallback](docs/MULTI-MODEL-FALLBACK.md) — chain semantics, circuit breakers, tuning

## License

MIT — zero lock-in, on your terms. See [LICENSE](../../LICENSE).
