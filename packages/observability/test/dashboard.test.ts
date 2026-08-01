import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createAIOps } from '../src/index.ts';
import { HealthMonitor } from '../src/health.ts';
import { AnalyticsEngine } from '../src/analytics.ts';
import { AlertEngine } from '../src/alerts.ts';
import { CostTracker } from '../src/cost.ts';
import { DegradationDetector } from '../src/degradation.ts';
import { DashboardBuilder } from '../src/dashboard.ts';
import { MetricRegistry } from '../src/metrics.ts';

test('createAIOps wires every subsystem', () => {
  const aiops = createAIOps();
  assert.ok(aiops.metrics instanceof MetricRegistry);
  assert.ok(aiops.health instanceof HealthMonitor);
  assert.ok(aiops.analytics instanceof AnalyticsEngine);
  assert.ok(aiops.alerts instanceof AlertEngine);
  assert.ok(aiops.cost instanceof CostTracker);
  assert.ok(aiops.degradation instanceof DegradationDetector);
  assert.ok(aiops.dashboard instanceof DashboardBuilder);
});

test('end-to-end: telemetry → alert → snapshot → HTML', () => {
  const aiops = createAIOps();
  const now = Date.now();

  // Health telemetry: pm healthy, coder slow + erroring.
  aiops.health.ingest({ agentId: 'pm', workspaceId: 'core', timestamp: now, latencyMs: 120, error: false, promptTokens: 100, completionTokens: 80, model: 'qwen3' });
  aiops.health.ingest({ agentId: 'coder', workspaceId: 'core', timestamp: now, latencyMs: 3000, error: false, promptTokens: 200, completionTokens: 150, model: 'qwen3' });
  aiops.health.ingest({ agentId: 'coder', workspaceId: 'core', timestamp: now + 1, latencyMs: 4000, error: true, promptTokens: 50, completionTokens: 10, model: 'qwen3' });

  // Cost tracking: tiny budget so the alert fires immediately.
  aiops.cost.setPricing('qwen3', { promptPerM: 0.15, completionPerM: 0.6 });
  aiops.cost.record('pm', 'qwen3', 100, 80, { workspaceId: 'core', timestamp: now });
  aiops.cost.addBudget({
    id: 'core',
    name: 'core daily',
    scope: { workspaceId: 'core' },
    period: 'day',
    limitUsd: 0.00005,
    alertAtPct: 0.5,
  });

  // Alert rule: p95 latency >= 2s.
  aiops.alerts.addRule({
    id: 'latency',
    name: 'High p95 latency',
    metric: 'p95_latency_ms',
    op: '>=',
    threshold: 2000,
    severity: 'warning',
  });
  aiops.alerts.evaluate(aiops.health.snapshot('coder', 300), now);
  aiops.alerts.syncIncidents(now);

  const snap = aiops.dashboard.snapshot();
  assert.equal(snap.fleet.agents, 2);
  assert.equal(snap.fleet.critical, 1); // coder error rate 0.5
  assert.equal(snap.firingAlerts.length, 1);
  assert.equal(snap.incidents.length, 1);
  assert.equal(snap.budgets.length, 1);

  const html = aiops.dashboard.renderHtml();
  assert.match(html, /EyWALink AIOps Dashboard/);
  assert.match(html, /badge warning/);
  assert.match(html, /pm/);
  assert.match(html, /coder/);
});

test('dashboard renders empty states without telemetry', () => {
  const aiops = createAIOps();
  const html = aiops.dashboard.renderHtml();
  assert.match(html, /No agent telemetry yet/);
  assert.match(html, /No firing alerts/);
});
