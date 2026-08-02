import test from 'node:test';
import assert from 'node:assert/strict';

import {
  HealthMonitor,
  AlertEngine,
  AnalyticsEngine,
  CostTracker,
  DegradationDetector,
  costForTokens,
  DEFAULT_PRICING,
} from '../dist/index.js';

const T0 = Date.now();

function sample(overrides = {}) {
  return {
    agentId: 'agent-a',
    workspaceId: 'ws-1',
    timestamp: T0,
    latencyMs: 100,
    error: false,
    promptTokens: 500,
    completionTokens: 200,
    model: 'qwen3:8b',
    provider: 'ollama',
    ...overrides,
  };
}

test('health monitor computes percentiles, error rate, and score', () => {
  const m = new HealthMonitor();
  for (let i = 0; i < 100; i++) {
    m.ingest(sample({ latencyMs: 100 + i, timestamp: T0 + i }));
  }
  const snap = m.snapshot('agent-a', 300);
  assert.equal(snap.requests, 100);
  assert.equal(snap.errors, 0);
  assert.equal(snap.errorRate, 0);
  assert.ok(snap.p50LatencyMs >= 149 && snap.p50LatencyMs <= 150, `p50=${snap.p50LatencyMs}`);
  assert.ok(snap.p95LatencyMs >= 194 && snap.p95LatencyMs <= 196, `p95=${snap.p95LatencyMs}`);
  assert.equal(snap.status, 'healthy');
  assert.equal(snap.workspaceId, 'ws-1');
  assert.equal(snap.model, 'qwen3:8b');
  assert.ok(snap.tokensPerMinute > 0);
});

test('health monitor flags critical error rates and unknown agents', () => {
  const m = new HealthMonitor();
  for (let i = 0; i < 10; i++) m.ingest(sample({ error: true, timestamp: T0 + i }));
  const snap = m.snapshot('agent-a', 300);
  assert.equal(snap.errorRate, 1);
  assert.equal(snap.status, 'critical');
  assert.ok(snap.score < 50);

  const unknown = m.snapshot('nobody', 300);
  assert.equal(unknown.status, 'unknown');
  assert.equal(unknown.requests, 0);
});

test('health monitor prunes old samples', () => {
  const m = new HealthMonitor();
  m.ingest(sample({ timestamp: T0 - 3600_000 }));
  m.ingest(sample({ timestamp: T0 }));
  const removed = m.prune(T0 - 60_000);
  assert.equal(removed, 1);
  assert.equal(m.snapshot('agent-a', 300).requests, 1);
});

test('alert engine fires, dedupes, acknowledges, resolves', () => {
  const engine = new AlertEngine();
  engine.addRule({
    id: 'r1',
    name: 'high error rate',
    metric: 'error_rate',
    op: '>=',
    threshold: 0.2,
    severity: 'critical',
    cooldownSeconds: 60,
  });
  const m = new HealthMonitor();
  for (let i = 0; i < 10; i++) m.ingest(sample({ error: i < 3, timestamp: T0 + i }));
  const snap = m.snapshot('agent-a', 300);

  const fired = engine.evaluate(snap, T0 + 1000);
  assert.equal(fired.length, 1);
  assert.equal(fired[0].severity, 'critical');
  assert.equal(engine.firingAlerts().length, 1);

  // Same snapshot again: cooldown prevents re-fire but refresh value.
  const fired2 = engine.evaluate(snap, T0 + 2000);
  assert.equal(fired2.length, 0);

  const ack = engine.acknowledge(fired[0].id, 'sre', T0 + 3000);
  assert.equal(ack?.status, 'acknowledged');
  const res = engine.resolve(fired[0].id, T0 + 4000);
  assert.equal(res?.status, 'resolved');
  assert.equal(engine.firingAlerts().length, 0);
});

test('alert engine scope filter excludes other agents', () => {
  const engine = new AlertEngine();
  engine.addRule({
    id: 'r2',
    name: 'agent-a only',
    metric: 'error_rate',
    op: '>=',
    threshold: 0.5,
    severity: 'warning',
    scope: { agentId: 'agent-a' },
  });
  const m = new HealthMonitor();
  for (let i = 0; i < 10; i++)
    m.ingest(sample({ agentId: 'agent-b', error: true, timestamp: T0 + i }));
  assert.equal(engine.evaluate(m.snapshot('agent-b', 300), T0 + 1000).length, 0);
});

test('incidents group alerts and follow lifecycle', () => {
  const engine = new AlertEngine();
  engine.addRule({
    id: 'r3',
    name: 'error spike',
    metric: 'error_rate',
    op: '>=',
    threshold: 0.2,
    severity: 'critical',
  });
  const m = new HealthMonitor();
  for (let i = 0; i < 10; i++) m.ingest(sample({ error: true, timestamp: T0 + i }));
  engine.evaluate(m.snapshot('agent-a', 300), T0 + 1000);

  const created = engine.syncIncidents(T0 + 1000);
  assert.equal(created.length, 1);
  const inc = engine.getIncidents()[0];
  assert.equal(inc.status, 'open');
  assert.ok(inc.agentIds.includes('agent-a'));

  engine.acknowledgeIncident(inc.id, 'sre', T0 + 2000);
  assert.equal(engine.getIncident(inc.id)?.status, 'acknowledged');
  engine.resolveIncident(inc.id, T0 + 3000);
  assert.equal(engine.getIncident(inc.id)?.status, 'resolved');
});

test('analytics aggregates per agent and workspace', () => {
  const a = new AnalyticsEngine();
  a.ingest(sample({ agentId: 'agent-a', latencyMs: 100, timestamp: T0 }));
  a.ingest(sample({ agentId: 'agent-a', latencyMs: 900, error: true, timestamp: T0 + 1 }));
  a.ingest(sample({ agentId: 'agent-b', latencyMs: 50, timestamp: T0 + 2 }));

  const pa = a.agentPerformance('agent-a', 300);
  assert.equal(pa.requests, 2);
  assert.equal(pa.errors, 1);
  assert.equal(pa.errorRate, 0.5);
  assert.ok(pa.p95LatencyMs >= 800, `p95=${pa.p95LatencyMs}`); // linear interpolation of [100, 900]

  const ws = a.workspacePerformance(300);
  assert.equal(ws.length, 1);
  assert.equal(ws[0].agents, 2);
  assert.equal(ws[0].requests, 3);
});

test('cost tracker converts tokens and fires budget alerts once per period', () => {
  const t = new CostTracker({
    pricing: { 'qwen3:8b': { promptPerM: 0.15, completionPerM: 0.6 } },
  });
  t.record('agent-a', 'qwen3:8b', 1_000_000, 0, { workspaceId: 'ws-1', timestamp: T0 });
  assert.equal(t.spend(undefined, 'day', T0), 0.15);
  assert.equal(
    costForTokens('qwen3:8b', 1_000_000, 1_000_000, {
      'qwen3:8b': { promptPerM: 0.15, completionPerM: 0.6 },
    }),
    0.75,
  );
  assert.ok(DEFAULT_PRICING.promptPerM > 0);

  t.addBudget({
    id: 'b1',
    name: 'ws-1 daily',
    scope: { workspaceId: 'ws-1' },
    period: 'day',
    limitUsd: 0.1,
    alertAtPct: 1.0,
  });
  const alerts = t.checkBudgets(T0);
  assert.equal(alerts.length, 1);
  assert.equal(alerts[0].budgetId, 'b1');
  assert.ok(Math.abs(alerts[0].pctUsed - 1.5) < 1e-9, `pctUsed=${alerts[0].pctUsed}`);

  // Same period: no duplicate alert.
  assert.equal(t.checkBudgets(T0 + 1000).length, 0);
});

test('degradation detector triggers retraining after persistent drift', () => {
  const reports = [];
  const d = new DegradationDetector(
    { retrainAfterWindows: 3 },
    { onReport: (r) => reports.push(r) },
  );

  const healthy = {
    agentId: 'agent-a',
    model: 'qwen3:8b',
    requests: 100,
    errorRate: 0.02,
    p95LatencyMs: 300,
  };
  // First: establish baseline (healthy).
  assert.equal(d.evaluate(healthy, T0), null);

  const degraded = { ...healthy, errorRate: 0.3 };
  const r1 = d.evaluate(degraded, T0 + 1000);
  assert.ok(r1 && r1.status === 'degraded' && r1.retrainTriggered === false);
  const r2 = d.evaluate(degraded, T0 + 2000);
  assert.ok(r2 && r2.status === 'degraded');
  const r3 = d.evaluate(degraded, T0 + 3000);
  assert.ok(r3 && r3.status === 'retrain_triggered' && r3.retrainTriggered === true);
  assert.ok(reports.length >= 3);

  // Recovery back to baseline stops the trigger.
  assert.equal(d.evaluate(healthy, T0 + 4000), null);
});
