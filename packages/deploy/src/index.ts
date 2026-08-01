/** @packageDocumentation
 * @module @eywalink/deploy
 *
 * Deployment tooling and infrastructure templates for the EyWALink
 * private AI reference implementation.
 *
 * Artifacts:
 *  - `docker-compose.yml`        — full stack (gateway, SGLang, vLLM, Ollama,
 *                                  Qdrant, Prometheus, Grafana, Phoenix, Loki,
 *                                  OTel collector)
 *  - `.env.example`              — configuration template
 *  - `prometheus/`               — scrape configuration
 *  - `grafana-dashboards/`       — provisioned datasource + dashboard
 *  - `otel-collector/`           — OTLP pipeline to Phoenix
 *  - `docs/`                     — quickstart, deployment, VRAM, fallback guides
 */

export const DEPLOY_VERSION = '0.1.0';

/** Names of the compose services that form the core stack. */
export const CORE_SERVICES = [
  'gateway',
  'sglang',
  'qdrant',
  'prometheus',
  'grafana',
  'phoenix',
  'loki',
  'otel-collector',
] as const;

/** Services behind the `fallback` compose profile. */
export const FALLBACK_SERVICES = ['vllm', 'ollama'] as const;
