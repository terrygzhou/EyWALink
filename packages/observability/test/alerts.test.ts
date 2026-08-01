import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlertEngine } from '../src/alerts.ts';
import { HealthMonitor } from '../src/health.ts';
import type { AlertEvent } from '../src/types.ts';

const T0 = Date.now();

function errSnapshot(errorRate: number) {
  const m = new HealthMonitor();
  for (let i = 0; i < 10; i++) {
    m.ingest({
      agentId: 'agent-a',
      workspaceId: 'ws-1',
      timestamp: T0 + i,
      latencyMs: 100,
      error: i / 10 < errorRate,
      promptTokens: 100,
      completionTokens: 80,
    });
  }
  return m.snapshot('agent-a', 300);
}

test('rule fires, cooldown dedupes, acknowledge and resolve', () => {
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

  const fired = engine.evaluate(errSnapshot(0.5), T0 + 1000);
  assert.equal(fired.length, 1);
  assert.equal(fired[0].severity, 'critical');
  assert.equal(engine.firingAlerts().length, 1);

  // Same condition again: no re-fire, value refreshed.
  assert.equal(engine.evaluate(errSnapshot(0.6), T0 + 2000).length, 0);
  assert.equal(engine.firingAlerts()[0].value, 0.6);

  const ack = engine.acknowledge(fired[0].id, 'sre', T0 + 3000);
  assert.equal(ack?.status, 'acknowledged');
  const res = engine.resolve(fired[0].id, T0 + 4000);
  assert.equal(res?.status, 'resolved');
  assert.equal(engine.firingAlerts().length, 0);
});

test('scope filter excludes other agents', () => {
  const engine = new AlertEngine();
  engine.addRule({
    id: 'r2',
    name: 'agent-a only',
    metric: 'error_rate',
    op: '>=',
    threshold: 0.2,
    severity: 'warning',
    scope: { agentId: 'agent-a' },
  });
  const m = new HealthMonitor();
  for (let i = 0; i < 10; i++) {
    m.ingest({
      agentId: 'agent-b',
      timestamp: T0 + i,
      latencyMs: 100,
      error: true,
      promptTokens: 10,
      completionTokens: 10,
    });
  }
  assert.equal(engine.evaluate(m.snapshot('agent-b', 300), T0 + 1000).length, 0);
});

test('incidents group alerts and follow the lifecycle', () => {
  const engine = new AlertEngine();
  engine.addRule({
    id: 'r3',
    name: 'error spike',
    metric: 'error_rate',
    op: '>=',
    threshold: 0.2,
    severity: 'critical',
  });
  engine.evaluate(errSnapshot(0.5), T0 + 1000);

  const created = engine.syncIncidents(T0 + 1000);
  assert.equal(created.length, 1);
  const inc = engine.getIncidents()[0];
  assert.equal(inc.status, 'open');
  assert.ok(inc.agentIds.includes('agent-a'));

  engine.acknowledgeIncident(inc.id, 'sre', T0 + 2000);
  assert.equal(engine.getIncident(inc.id)?.status, 'acknowledged');
  engine.resolveIncident(inc.id, T0 + 3000);
  assert.equal(engine.getIncident(inc.id)?.status, 'resolved');
  assert.equal(engine.active().length, 0);
});

test('notifier is called on alert transitions', () => {
  const seen: AlertEvent[] = [];
  const engine = new AlertEngine({ notify: (a) => seen.push(a) });
  engine.addRule({
    id: 'r4',
    name: 'latency',
    metric: 'p95_latency_ms',
    op: '>=',
    threshold: 2000,
    severity: 'warning',
  });
  const m = new HealthMonitor();
  for (let i = 0; i < 5; i++) {
    m.ingest({
      agentId: 'agent-a',
      timestamp: T0 + i,
      latencyMs: 5000,
      error: false,
      promptTokens: 10,
      completionTokens: 10,
    });
  }
  const fired = engine.evaluate(m.snapshot('agent-a', 300), T0 + 1000);
  assert.equal(fired.length, 1);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].id, fired[0].id);
});
