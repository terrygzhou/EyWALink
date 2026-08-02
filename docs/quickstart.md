# EyWALink Private AI — Quickstart

Get the reference implementation running in under 10 minutes. Zero lock-in,
budget-friendly, and fully self-hosted.

## Prerequisites

- Docker with the Compose plugin (`docker compose version`)
- NVIDIA GPU with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (for SGLang/vLLM)
- ~40 GB VRAM for the default 27B NVFP4 model (scale down for smaller models)

## 1. Clone and configure

```bash
git clone https://github.com/terrygzhou/EyWALink.git
cd EyWALink/packages/deploy

cp .env.example .env
# Edit .env: set PRIMARY_MODEL, GPU pins, ports
```

Key settings in `.env`:

| Setting                   | Default                    | Meaning                              |
| ------------------------- | -------------------------- | ------------------------------------ |
| `PRIMARY_MODEL`           | `nvidia/Qwen3.6-27B-NVFP4` | Model served by SGLang               |
| `FALLBACK_CHAIN`          | `sglang,vllm,ollama`       | Fallback order (comma-separated)     |
| `MEM_FRACTION_STATIC`     | `0.89`                     | SGLang VRAM fraction for KV cache    |
| `SGLANG_GPU` / `VLLM_GPU` | `all`                      | Pin GPUs per service (e.g. `0`, `1`) |
| `VLLM_GPU_MEM_UTIL`       | `0.90`                     | vLLM memory utilization target       |

## 2. Start the core stack

```bash
docker compose up -d
```

This starts the gateway (FastAPI), SGLang (primary model), Qdrant (vector
DB), Prometheus, Grafana, Phoenix, Loki, and the OTel Collector.

## 3. (Optional) Enable fallback backends

vLLM and Ollama are behind the `fallback` profile so they don't consume GPU
memory unless you want them:

```bash
docker compose --profile fallback up -d
```

## 4. Verify

```bash
# Health + fallback chain
curl http://localhost:8000/v1/health

# Chat completion (auto-fallback across the chain)
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hello!"}]}'

# List models
curl http://localhost:8000/v1/models
```

## 5. Dashboards

| Service    | URL                        | Credentials   |
| ---------- | -------------------------- | ------------- |
| API docs   | http://localhost:8000/docs | —             |
| Prometheus | http://localhost:9091      | —             |
| Grafana    | http://localhost:3001      | admin / admin |
| Phoenix    | http://localhost:6007      | —             |

## 6. Stop

```bash
docker compose down          # stop (keeps volumes)
docker compose down -v       # stop and wipe data
```

## Next steps

- Full operations guide: [deploy-guide.md](deploy-guide.md)
- Adjust VRAM and GPU pinning for your hardware in `.env` (see
  [deploy-guide.md](deploy-guide.md#gpu-and-vram-management))
