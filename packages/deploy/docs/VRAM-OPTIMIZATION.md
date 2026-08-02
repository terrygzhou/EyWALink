# GPU & VRAM Optimization

The goal: serve as much model as fits, with enough headroom that the runtime
doesn't OOM, while leaving a fallback provider ready. Every knob below is in
`.env`.

## 1. Model sizing cheat sheet (NVFP4 / FP8-quantized)

| Model size    | Approx VRAM (quantized) | Usable on               |
| ------------- | ----------------------- | ----------------------- |
| 7B–8B         | 6–8 GB                  | consumer GPUs (8–12 GB) |
| 14B           | 10–14 GB                | 16 GB GPUs              |
| 27B (default) | 18–22 GB                | 24 GB+ GPUs             |
| 32B           | 22–26 GB                | 24–32 GB GPUs           |
| 70B           | 45–55 GB                | 2×24 GB or 80 GB        |

NVFP4/FP8 quantized checkpoints roughly halve VRAM vs. BF16. If a model fails
to load, try the quantized checkpoint first, then a smaller model.

## 2. SGLang knobs

```
MEM_FRACTION_STATIC=0.89        # fraction of VRAM for KV cache + weights
CONTEXT_LENGTH=128000           # max context window
```

- **`MEM_FRACTION_STATIC`** — SGLang preallocates this fraction. `0.89` is a
  good default on dedicated GPUs. Lower it (e.g. `0.80`) when:
  - the GPU also runs other workloads (display, other containers)
  - you see CUDA OOM during prefill with large batches
- **`CONTEXT_LENGTH`** — shorter context = smaller KV cache = more concurrent
  requests fit. `--allow-auto-truncate` lets SGLang truncate over-length
  prompts instead of erroring.
- **`--chunked-prefill-size 4096`** — chunks prefill, reducing peak memory
  spikes and improving interleaving with decode.

## 3. vLLM knobs

```
VLLM_GPU_MEM_UTIL=0.90          # fraction of GPU memory the engine may use
```

- `--enable-prefix-caching` is on by default in the compose file — it reuses
  KV cache across requests with shared prefixes (massive win for RAG).
- If vLLM shares a GPU with SGLang, set `VLLM_GPU_MEM_UTIL=0.45` and
  `SGLANG_MEM_FRACTION_STATIC=0.45` so the two don't fight.

## 4. Two-GPU reference layout

```
SGLANG_GPU=0          # primary on GPU 0
VLLM_GPU=1            # fallback on GPU 1
SGLANG_GPU_COUNT=1
VLLM_GPU_COUNT=1
MEM_FRACTION_STATIC=0.90
VLLM_GPU_MEM_UTIL=0.90
```

## 5. CPU-only mode (no GPU)

For a pure-CPU evaluation environment, run only the Ollama fallback:

```bash
# .env
FALLBACK_CHAIN=ollama
```

and skip the SGLang/vLLM GPU services:

```bash
docker compose up -d                       # core stack minus GPU services
docker compose --profile fallback up -d ollama
```

Ollama runs on CPU out of the box; expect 5–20 tok/s for 7–8B models.

## 6. Monitoring VRAM

- `nvidia-smi -l 1` for live usage.
- Prometheus: SGLang and vLLM both export GPU memory metrics
  (`nv_gpu_mem_used_bytes` / vLLM's `vllm:gpu_cache_usage_perc`).
- The Grafana dashboard shows provider health; add a
  `gpu_mem_used / gpu_mem_total` panel for capacity planning.

## 7. Rules of thumb

- Leave ≥10% VRAM headroom unless you know your workload.
- Small context + big batch > big context + small batch for throughput.
- Quantized weights (NVFP4/FP8/AWQ) are the single biggest lever for fitting
  bigger models on the same card.
