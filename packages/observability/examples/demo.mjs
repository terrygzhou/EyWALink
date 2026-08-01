#!/usr/bin/env node
/**
 * EyWALink AIOps toolkit demo.
 *
 * Simulates a small agent fleet (pm, architect, coder), feeds health/cost
 * telemetry through the pipeline, fires an alert + incident + budget alert +
 * degradation report, and writes a standalone HTML dashboard.
 *
 * Run:  node examples/demo.mjs
 * Out:  examples/dashboard.html
 */
import { createAIOps } from '../src/index.ts';
import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const aiops = createAIOps();
const now = Date.now();
const agents = ['pm', 'architect', 'coder'];
const workspaces = { pm: 'core', architect: 'core', coder: 'platform' };

// Fleet telemetry: 60s of simulated traffic. coder degrades (errors + slow).
for (let t = 0; t < 60; t += 5) {
  for (const agent of agents) {
    const slow = agent === 'coder' && t > 25;
    const err = agent === 'coder' && t > 35 && t % 10 === 0;
    aiops.health.ingest({
      agentId: agent,
      workspaceId: workspaces[agent],
      timestamp: now - 60_000 + t * 1000,
      latencyMs: slow ? 3000 + Math.random() * 1500 : 120 + Math.random() * 200,
      error: err,
      promptTokens: 800 + Math.floor(Math.random() * 400),
      completionTokens: 400 + Math.floor(Math.random() * 200),
      model: 'qwen3:8b',
      provider: 'ollama',
    });
    aiops.cost.record(agent, 'qwen3:8b', 1000, 500, {
      workspaceId: workspaces[agent],
      timestamp: now - 60_000 + t * 1000,
    });
  }
}

// Pricing + budgets.
aiops.cost.setPricing('qwen3:8b', { promptPerM: 0.15, completionPerM: 0.6 });
aiops.cost.addBudget({
  id: 'platform-day', name: 'platform daily budget',
  scope: { workspaceId: 'platform' }, period: 'day',
  limitUsd: 0.02, alertAtPct: 0.8,
});

// Alert rules + evaluation.
aiops.alerts.addRule({
  id: 'err-rate', name: 'High error rate',
  metric: 'error_rate', op: '>=', threshold: 0.2,
  severity: 'critical', cooldownSeconds: 120,
});
aiops.alerts.addRule({
  id: 'p95-latency', name: 'High p95 latency',
  metric: 'p95_latency_ms', op: '>=', threshold: 2000,
  severity: 'warning', cooldownSeconds: 120,
});
for (const agent of agents) {
  aiops.alerts.evaluate(aiops.health.snapshot(agent, 300));
  aiops.degradation.evaluate(aiops.health.snapshot(agent, 300));
}
aiops.alerts.syncIncidents();

// Summary to stdout + dashboard to file.
const snap = aiops.dashboard.snapshot();
console.log(`fleet: ${snap.fleet.agents} agents ` +
  `(healthy ${snap.fleet.healthy}, degraded ${snap.fleet.degraded}, critical ${snap.fleet.critical})`);
console.log(`firing alerts: ${snap.firingAlerts.length}, incidents: ${snap.incidents.length}, ` +
  `budget alerts: ${snap.budgets.length}, degradation: ${snap.degradation.length}`);
console.log(`day cost: $${snap.fleet.totalCostUsd.toFixed(4)}`);

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, 'dashboard.html');
writeFileSync(out, aiops.dashboard.renderHtml());
console.log(`dashboard written to ${out}`);

// Prometheus exposition sample (scrape this from /metrics).
const reg = aiops.metrics;
const req = reg.counter('eywalink_agent_requests_total', 'Total agent requests');
for (const a of agents) req.inc({ agent: a }, 12);
console.log('\n--- /metrics sample ---');
console.log(reg.render());
