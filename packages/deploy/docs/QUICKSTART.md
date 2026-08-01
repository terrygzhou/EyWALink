# Quickstart — EyWALink Private AI Reference Implementation

## 1. Prerequisites

- Docker Engine 24+ with Compose v2 (`docker compose version`)
- NVIDIA Container Toolkit installed and configured:
  - `nvidia-ctk runtime configure --runtime=docker && systemctl restart docker`
  - Verify: `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi`
- Hardware: see [VRAM-OPTIMIZATION.md](VRAM-OPTIMIZATION.md) for model sizing.
  Default 27B NVFP4 model ≈ 18–22GB VRAM. For smaller GPUs, set `PRIMARY_MODEL`
  to a 7B–8B model (e.g. `Qwen/Qwen2.5-7B-Instruct`).

## 2. Configure

```bash
cd packages/deploy
cp .env.example .env
```

Edit `.env` at minimum:

- `PRIMARY_MODEL` — the model SGLang should serve
- `FALLBACK_VLLM_MODEL`, `FALLBACK_OLLAMA_MODEL` — fallback models
- `HF_CACHE_PATH` / volumes — where model weights live
- Ports, if 8000/8080/6333/9091/3001/6007/3101 conflict with existing services

## 3. Start

```bash
# Core stack (gateway, sglang, qdrant, prometheus, grafana, phoenix, loki, otel)
docker compose up -d

# Add vLLM + Ollama fallback providers
docker compose --profile fallback up -d
```

First startup downloads model weights into the `eywalink-hf-cache` volume.
Subsequent starts reuse them.

## 4. Verify

| Check                          | Command                                          |
| ------------------------------ | ------------------------------------------------ |
| Gateway health                 | `curl localhost:8000/v1/health`                  |
| Model list                     | `curl localhost:8000/v1/models`                  |
| Prometheus targets             | `curl localhost:9091/api/v1/targets`             |
| Grafana                        | http://localhost:3001 (admin / admin)            |
| Phoenix traces                 | http://localhost:6007                            |

## 5. Send a request

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia/Qwen3.6-27B-NVFP4",
       "messages":[{"role":"user","content":"Hello!"}]}'
```

Response includes `"provider": "sglang"` so you can confirm which backend served it.

## 6. Test the fallback (optional)

Stop the primary and watch the chain:

```bash
docker compose stop sglang
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"ping"}]}'
# provider will be vllm or ollama
docker compose start sglang
```

## Troubleshooting

- **`could not select device driver "" with capabilities: [[gpu]]`** — NVIDIA
  Container Toolkit not installed/configured. See step 1.
- **Gateway 503 "all providers failed"** — every provider is down or the
  chain only lists providers you didn't start. Check `GATEWAY_FALLBACK_CHAIN`
  and `docker compose ps`.
- **OOM / model fails to load** — model too big for VRAM. Reduce
  `MEM_FRACTION_STATIC` or switch `PRIMARY_MODEL` to a smaller model.
- **Ports already in use** — change ports in `.env` (all services are
  configurable).
- **Phoenix/Grafana blank** — allow 30–60s for first boot and metric scrape.
