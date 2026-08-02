# EyWALink Private AI — Deployment Guide

Production-oriented guide for the self-hosted reference implementation:
Docker Compose stack with SGLang (model serving), Qdrant (vector DB), FastAPI
gateway (application layer), and a full observability suite.

## Architecture

```
                        ┌─────────────────────────────────────────┐
                        │              Gateway (FastAPI)          │
   OpenAI-compatible    │  /v1/chat/completions  (auto-fallback)  │
   clients ───────────► │  /v1/models  /v1/health  /metrics       │
                        └───────┬──────────┬──────────┬───────────┘
                                │          │          │
                        ┌───────▼───┐ ┌────▼────┐ ┌───▼──────┐
                        │  SGLang   │ │  vLLM   │ │  Ollama  │
                        │ (primary) │ │(fallback)││(fallback)│
                        └───────┬───┘ └─────────┘ └──────────┘
                                │
                        ┌───────▼───────┐   ┌─────────────────────────┐
                        │    Qdrant     │   │ OTel Collector          │
                        │  (vector DB)  │   │  └─ Phoenix (traces)    │
                        └───────────────┘   │  └─ Prometheus/Grafana  │
                                            │  └─ Loki (logs)         │
                                            └─────────────────────────┘
```

All model servers speak the OpenAI `/v1/chat/completions` protocol, so the
gateway is provider-agnostic. Adding or swapping a backend means pointing the
gateway at another OpenAI-compatible endpoint — no vendor lock-in.

## Services

| Service          | Purpose                                      | Default port |
| ---------------- | -------------------------------------------- | ------------ |
| `gateway`        | FastAPI app layer, fallback routing          | 8000         |
| `sglang`         | Primary LLM serving (GPU)                    | 8080         |
| `vllm`           | Secondary LLM (profile: fallback)            | 8001         |
| `ollama`         | CPU-friendly last resort (profile: fallback) | 11434        |
| `qdrant`         | Vector DB for RAG                            | 6333/6334    |
| `prometheus`     | Metrics scraping                             | 9091         |
| `grafana`        | Dashboards (provisioned)                     | 3001         |
| `phoenix`        | LLM trace observability                      | 6007         |
| `loki`           | Log aggregation                              | 3101         |
| `otel-collector` | OTLP ingestion, fan-out                      | 4319/4320    |

## GPU and VRAM Management

The stack is tuned to run a 27B-class NVFP4 model on a single 32 GB GPU with
headroom for the gateway and Qdrant.

- **`MEM_FRACTION_STATIC` (SGLang)** — fraction of VRAM reserved for the KV
  cache. `0.89` leaves ~11% headroom. Lower it (e.g. `0.80`) when sharing a
  GPU with other services, or when you see OOM during load spikes.
- **`SGLANG_GPU` / `VLLM_GPU`** — pin specific GPUs per service
  (`0`, `1`, `0,1`). Set both to the same value to colocate, or different
  values to dedicate GPUs.
- **`VLLM_GPU_MEM_UTIL`** — vLLM's own memory utilization target (default
  `0.90`).
- **`shm_size: 32g`** — required for the CUDA/FlashInfer kernels; raise if
  you hit shared-memory errors.
- **`hf_cache` volume** — named volume for the Hugging Face cache; model
  weights download once and are shared across services and restarts.

> SGLang is the primary and is always started. vLLM and Ollama only run when
> the `fallback` profile is enabled, so a minimal deployment uses just the
> SGLang GPU.

## Multi-Model Fallback Chain

The gateway walks providers in `FALLBACK_CHAIN` order (default
`sglang,vllm,ollama`):

1. A per-provider circuit breaker trips after 3 consecutive failures and
   stays open for 30 s (cooldown), so a dead backend stops being hammered.
2. After cooldown, a half-open probe (`GET /health`) decides whether the
   provider may rejoin the chain.
3. If every provider fails, the gateway returns `503` with per-provider
   diagnostics (`attempted`, `last_error`, `health`) so operators can see
   exactly what happened.
4. Metrics track fallback events, provider up/down, and circuit state:
   `eywalink_fallbacks_total`, `eywalink_provider_up`,
   `eywalink_provider_circuit_open`.

Provider config is env-driven in `.env` (URL, model, timeout per provider),
so the chain can be reordered or extended without code changes.

## Observability

- **Prometheus** scrapes the gateway (`/metrics`), SGLang, Qdrant, and the
  OTel Collector. Retention: 15 days.
- **Grafana** is provisioned with a Prometheus datasource and the
  "EyWALink Private AI" dashboard (request rate, latency histograms, fallback
  events, provider health, token usage). Login: `admin` / `admin` (set via
  `GRAFANA_USER` / `GRAFANA_PASSWORD`).
- **Phoenix** receives OTLP traces via the collector — full request-level
  trace inspection for LLM calls (prompt/completion spans from FastAPI and
  httpx instrumentation).
- **Loki** ingests logs (collector debug exporter fan-out).

The gateway never crashes if observability is down: tracing setup is wrapped
in try/except and instrumentation is optional at import time.

## Deployment Checklist

- [ ] NVIDIA Container Toolkit installed and `nvidia-smi` works
- [ ] `.env` created from `.env.example`; model names and ports correct
- [ ] `PRIMARY_MODEL` is set; the HF cache volume will download weights on first start
- [ ] GPU pins set (`SGLANG_GPU`, `VLLM_GPU`) to avoid oversubscription
- [ ] Ports are free on the host (defaults avoid common conflicts: Prometheus
      9091, Grafana 3001, Phoenix 6007, Loki 3101)
- [ ] `docker compose config` validates
- [ ] `curl localhost:8000/v1/health` returns `ok` and the full fallback chain
- [ ] Grafana dashboard shows live metrics after a few requests

## Operations

```bash
# View logs
docker compose logs -f gateway sglang

# Restart one service
docker compose restart sglang

# Scale the gateway (stateless; requires a load balancer in front)
docker compose up -d --scale gateway=3

# Update images
docker compose pull && docker compose up -d

# Wipe everything
docker compose down -v
```

## Troubleshooting

| Symptom                            | Fix                                                                      |
| ---------------------------------- | ------------------------------------------------------------------------ |
| SGLang OOM at startup              | Lower `MEM_FRACTION_STATIC`, use a smaller `PRIMARY_MODEL`               |
| `shared memory` errors             | Raise `shm_size` (already 32g; check host `/dev/shm`)                    |
| Gateway 503 `all providers failed` | Check `attempted`/`health` in the response; restart the dead backend     |
| Model not found at startup         | Check the `eywalink-hf-cache` volume; allow time for the first download  |
| Grafana shows no data              | Verify Prometheus is up and dashboard datasource is `Prometheus`         |
| Traces missing in Phoenix          | Check `otel-collector` logs; gateway falls back silently if OTLP is down |

## Zero Lock-In Notes

- Every component is open source (SGLang, vLLM, Ollama, Qdrant, Prometheus,
  Grafana, Phoenix, Loki, OTel Collector) and runs in containers you control.
- The gateway speaks the OpenAI protocol to all backends; nothing binds you
  to a vendor's API shape.
- Config is 100% env-driven — the same images deploy on a workstation, a
  single VM, or a multi-GPU box with different `.env` files.
