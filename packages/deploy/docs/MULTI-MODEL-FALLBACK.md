# Multi-Model Fallback Chain

The gateway routes every `/v1/chat/completions` request through an ordered
chain of providers until one succeeds. This is the heart of the "no single
point of failure" story for self-hosted AI.

## Chain semantics

Configured via `FALLBACK_CHAIN` (default `sglang,vllm,ollama`).

```
request ──► sglang ──✗──► vllm ──✗──► ollama ──✗──► 503 + diagnostics
             │                 │                │
             └──✓ response     └──✓ response    └──✓ response
```

- A provider is tried only if its circuit breaker is closed (healthy).
- On failure (connection error, timeout, HTTP 4xx/5xx), the gateway records
  the failure, opens the next provider, and continues.
- The response includes `"provider": "<name>"` so callers can see who served.
- If every provider fails, the gateway returns **503** with the full health
  map so operators can debug without digging through logs.

## Circuit breakers

Each provider has a per-instance circuit breaker in
`packages/gateway/src/eywalink_gateway/providers.py`:

- **Trip**: 3 consecutive failures (`CIRCUIT_FAILURE_THRESHOLD`)
- **Open for**: 30s (`CIRCUIT_COOLDOWN_SECONDS`)
- **Half-open**: after cooldown, the first provider in the chain is probed
  with a lightweight `/health` call; success resets it, failure reopens it.

This prevents a dead primary from eating a timeout on _every_ request — after
3 failures it's skipped for 30s and the chain falls through immediately.

Tunable by editing the two module constants, or promote them to settings if
you need per-deployment control.

## Fallback reasons

Every fallback increments `eywalink_fallbacks_total{from_provider, reason}`.
Alert on this metric — a sustained rate means your primary is unhealthy and
the whole fleet is running on degraded capacity.

## Provider health

`GET /v1/health` returns per-provider status:

```json
{
  "status": "ok",
  "providers": {
    "sglang": {
      "circuit_open": false,
      "consecutive_failures": 0,
      "model": "nvidia/Qwen3.6-27B-NVFP4",
      "url": "http://sglang:8080"
    }
  },
  "fallback_chain": ["sglang", "vllm", "ollama"]
}
```

`GET /v1/models` lists every configured model with its `healthy` flag —
useful for clients that enumerate models before choosing.

## Adding a provider

Any OpenAI-compatible endpoint works. Steps:

1. Add settings in `config.py` (URL + model + timeout).
2. Add the provider to `providers` dict.
3. Add its name to `FALLBACK_CHAIN` in `.env`.
4. (Optional) add a compose service.

Because the protocol is OpenAI-compatible, SGLang, vLLM, Ollama, LM Studio,
llama.cpp server, and hosted endpoints (OpenAI, together.ai, etc.) are all
drop-in backends — that is the zero lock-in property: swap any layer without
touching application code.

## Tuning

- **Faster failover**: lower `CIRCUIT_FAILURE_THRESHOLD` / `COOLDOWN` if your
  primaries flap often.
- **Slower, larger primaries**: raise the per-provider `timeout` in
  `config.py` (default 300s for SGLang/vLLM).
- **Request-level model pinning**: pass `"model": "<name>"` in the request
  body; the gateway forwards it to whichever provider serves that model.
