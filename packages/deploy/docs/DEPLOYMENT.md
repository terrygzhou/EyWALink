# Deployment Guide — Production Hardening

This guide covers taking the reference implementation from `docker compose up`
to a hardened production deployment.

## 1. Networking & TLS

The gateway speaks OpenAI-compatible HTTP. In production, put a reverse proxy
in front of it:

- **Caddy** (recommended — automatic HTTPS, zero config):

  ```caddyfile
  ai.example.com {
      reverse_proxy gateway:8000
  }
  ```

- **Traefik** or **nginx** work equally well; the gateway needs no
  protocol-specific handling.

Do not expose SGLang, Qdrant, Prometheus, or Phoenix directly to the internet.
They sit on internal compose networks (`eywalink-ai`, `eywalink-obs`) and only
the gateway should be reachable.

## 2. Authentication

The gateway itself is deliberately unauthenticated (it's a reference
implementation). Add one of:

- **API key middleware** — inject a shared-secret header check in front of the
  gateway (Caddy `@matcher` + `header` or a small FastAPI dependency).
- **OIDC** — put an identity-aware proxy (oauth2-proxy, Authelia) in front.

## 3. Secrets

Never commit `.env`. The repository ships `.env.example` only.

```bash
cp .env.example .env
chmod 600 .env
# or use a secrets manager: docker compose --env-file <(sops -d .env) ...
```

Change `GRAFANA_PASSWORD` from the default immediately.

## 4. Persistent storage & backups

All state lives in named volumes:

| Volume              | Contents           | Backup strategy                    |
| ------------------- | ------------------ | ---------------------------------- |
| `eywalink-hf-cache` | model weights      | re-downloadable; skip or snapshot  |
| `eywalink-qdrant`   | vectors + payloads | **back up** (snapshot/restore API) |
| `eywalink-grafana`  | dashboards, users  | re-provisionable; snapshot         |
| `eywalink-phoenix`  | trace database     | snapshot                           |
| `eywalink-loki`     | logs               | snapshot                           |
| `eywalink-ollama`   | ollama models      | re-pullable                        |

Qdrant has a native snapshot API:

```bash
curl -X POST localhost:6333/collections/<name>/snapshots
# then copy the snapshot file from the volume
```

## 5. Resource limits

Compose already sets:

- Gateway memory limit (`GATEWAY_MEM_LIMIT=512m`)
- SGLang GPU reservation + `--mem-fraction-static`
- vLLM `--gpu-memory-utilization`

For multi-tenant hosts, pin GPUs per service with `SGLANG_GPU` / `VLLM_GPU`
(e.g. `SGLANG_GPU=0`, `VLLM_GPU=1`) and set `NVIDIA_VISIBLE_DEVICES` accordingly.

## 6. Observability in production

- Prometheus retention: 15d by default; raise `--storage.tsdb.retention.time`
  or ship to long-term storage via remote-write.
- Loki: add a retention policy in the Loki config for log volume control.
- Set alerting rules on the fallback metrics — a sustained fallback rate means
  your primary is unhealthy:

  ```yaml
  groups:
    - name: eywalink
      rules:
        - alert: PrimaryProviderDown
          expr: eywalink_provider_up{provider="sglang"} == 0
          for: 5m
          labels: { severity: critical }
        - alert: HighFallbackRate
          expr: sum(rate(eywalink_fallbacks_total[10m])) > 5
          for: 10m
          labels: { severity: warning }
  ```

## 7. Upgrades

- **SGLang / vLLM / Qdrant** — pin images to a specific tag in `.env` or the
  compose file after validating a release (the reference uses `latest` for
  ease of starting).
- Test model upgrades in a staging profile first; `MEM_FRACTION_STATIC` and
  context length are the two knobs most likely to need adjustment.

## 8. Observability stack ports

Default ports are chosen to avoid the most common local collisions
(Prometheus :9091, Grafana :3001, Phoenix :6007, Loki :3101, OTel :4319/4320).
All are overridable in `.env`.
