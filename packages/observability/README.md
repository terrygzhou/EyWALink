# @eywalink/observability — AIOps Toolkit

Zero-dependency monitoring, alerting, cost, and model-health tooling for AI
agent fleets. Built for EyWALink's "you own it, on your terms" philosophy: no
vendor SDKs, no telemetry exfil, no lock-in. Runs on stock Node.

## Capabilities

| Area                    | Module                         | What it does                                                                                                      |
| ----------------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| Agent health monitoring | `health.ts`                    | Rolling-window latency (p50/p95), error rate, token throughput, composite score, healthy/degraded/critical status |
| Metrics (Prometheus)    | `metrics.ts`                   | Counters, gauges, histograms; renders Prometheus text exposition for `/metrics`                                   |
| Alerting & incidents    | `alerts.ts`                    | Declarative rules (metric × operator × threshold), cooldown/dedup, alert lifecycle, incident grouping, notifiers  |
| Cost tracking           | `cost.ts`                      | Per-model pricing (USD/1M tokens), per-agent/workspace spend, budget alerts per period                            |
| Degradation detection   | `degradation.ts`               | EWMA baseline drift on error rate / p95 latency, consecutive-window degradation, automatic retraining trigger     |
| Analytics dashboard     | `analytics.ts`, `dashboard.ts` | Per-agent/workspace roll-ups; self-contained offline HTML dashboard                                               |

## Quick start

```ts
import { createAIOps } from '@eywalink/observability';

const aiops = createAIOps();

// 1. Record request telemetry (gateway middleware / agent hook).
aiops.health.ingest({
  agentId: 'pm',
  workspaceId: 'core',
  timestamp: Date.now(),
  latencyMs: 320,
  error: false,
  promptTokens: 500,
  completionTokens: 420,
  model: 'qwen3.6-27b',
  provider: 'sglang',
});

// 2. Track cost and set a budget.
aiops.cost.setPricing('qwen3.6-27b', { promptPerM: 0.15, completionPerM: 0.6 });
aiops.cost.record('pm', 'qwen3.6-27b', 500, 420, { workspaceId: 'core' });
aiops.cost.addBudget({
  id: 'core-monthly',
  name: 'Core workspace',
  scope: { workspaceId: 'core' },
  period: 'month',
  limitUsd: 50,
  alertAtPct: 0.8,
});

// 3. Alert on degraded health.
aiops.alerts.addRule({
  id: 'latency-p95',
  name: 'p95 latency high',
  metric: 'p95_latency_ms',
  op: '>=',
  threshold: 2000,
  severity: 'warning',
});

// 4. Feed degradation detector from periodic health snapshots.
const snap = aiops.health.snapshot('pm', 300);
aiops.degradation.evaluate(snap);

// 5. Render the offline dashboard.
const html = aiops.dashboard.renderHtml();
```

## Prometheus integration

The deploy stack (see `packages/deploy`) scrapes the gateway `/metrics`
endpoint. The AIOps toolkit exposes the expected metric family names:

- `agent_requests_total{agent,status}` — counter
- `agent_latency_seconds{agent}` — histogram
- `agent_tokens_total{agent}` — counter (input+output)
- `agent_health_status{agent}` — gauge (0 healthy / 1 degraded / 2 critical)
- `agent_budget_pct_used{budget,scope}` — gauge
- `agent_cost_usd_total{scope}` — counter
- `model_degradation_score{agent,model}` — gauge (0..1)
- `model_retraining_triggered{agent,model}` — gauge (0/1)

Provisioning:

- `prometheus/aiops-alerts.yml` — alert rules (error rate, latency, silence,
  budget, degradation, retraining). Mount into Prometheus
  `rule_files` alongside `packages/deploy/prometheus/prometheus.yml`.
- `grafana/aiops-fleet-dashboard.json` — agent-fleet dashboard. Drop into
  Grafana provisioning (`provisioning/dashboards`).

## Tests

```bash
pnpm test        # builds then runs node --test (no extra deps)
pnpm typecheck
```

Coverage: metrics exposition, health percentiles/status, alert lifecycle and
dedupe, incident grouping, cost/budget math, degradation triggers, dashboard
rendering, and an end-to-end bundle test (`test/observability.test.mjs`).
