/**
 * End-to-end example: wire the AIOps toolkit to agent telemetry and print
 * the dashboard. Run with:
 *
 *   node examples/demo.ts
 *
 * (Node 22+ runs TypeScript directly.)
 */

import { createAIOps } from '../src/index.ts';

const aiops = createAIOps();

// Simulate a fleet heartbeat.
const agents = ['pm', 'coder', 'qa', 'sre'];
const models = ['qwen3.6-27b', 'qwen3.6-35b-a3b'];
const now = Date.now();

for (let t = 0; t < 20; t++) {
  for (const agentId of agents) {
    const ok = agentId !== 'qa' || t % 5 !== 0; // qa fails ~20% of the time
    aiops.health.ingest({
      agentId,
      workspaceId: agentId === 'pm' ? 'core' : 'delivery',
      timestamp: now + t * 60_000,
      latencyMs: ok ? 150 + Math.random() * 900 : 5000 + Math.random() * 4000,
      error: !ok,
      promptTokens: 300 + Math.floor(Math.random() * 700),
      completionTokens: 200 + Math.floor(Math.random() * 500),
      model: models[t % models.length]!,
      provider: 'sglang',
    });
    aiops.cost.record(agentId, models[t % models.length]!, 500, 350, {
      workspaceId: agentId === 'pm' ? 'core' : 'delivery',
      timestamp: now + t * 60_000,
    });
  }
}

// Pricing + budgets.
aiops.cost.setPricing('qwen3.6-27b', { promptPerM: 0.15, completionPerM: 0.6 });
aiops.cost.setPricing('qwen3.6-35b-a3b', { promptPerM: 0.3, completionPerM: 1.1 });
aiops.cost.addBudget({
  id: 'core-monthly',
  name: 'Core workspace',
  scope: { workspaceId: 'core' },
  period: 'month',
  limitUsd: 10,
  alertAtPct: 0.5,
});

// Rules.
aiops.alerts.addRule({
  id: 'err-rate',
  name: 'Error rate high',
  metric: 'error_rate',
  op: '>=',
  threshold: 0.1,
  severity: 'critical',
});
aiops.alerts.addRule({
  id: 'latency',
  name: 'p95 latency high',
  metric: 'p95_latency_ms',
  op: '>=',
  threshold: 2000,
  severity: 'warning',
});

// Evaluate alerts + degradation for the fleet.
for (const agentId of agents) {
  const snap = aiops.health.snapshot(agentId, 300);
  aiops.alerts.evaluate(snap);
  aiops.degradation.evaluate(snap);
}
aiops.alerts.syncIncidents();

// Print summary + write dashboard.
const snap = aiops.dashboard.snapshot();
console.log(
  `Fleet: ${snap.fleet.agents} agents | healthy=${snap.fleet.healthy} degraded=${snap.fleet.degraded} critical=${snap.fleet.critical}`,
);
console.log(
  `Alerts firing: ${snap.firingAlerts.length} | incidents: ${snap.incidents.length} | budget alerts: ${snap.budgets.length}`,
);

import { writeFileSync } from 'node:fs';
writeFileSync('aiops-dashboard.html', aiops.dashboard.renderHtml());
console.log('Wrote aiops-dashboard.html');
