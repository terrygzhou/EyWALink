import { test } from 'node:test';
import assert from 'node:assert/strict';
import { HealthMonitor } from '../src/health.ts';
import type { HealthSample } from '../src/types.ts';

const T0 = Date.now();

function sample(overrides: Partial<HealthSample> = {}): HealthSample {
  return {
    agentId: 'pm',
    workspaceId: 'core',
    timestamp: T0,
    latencyMs: 200,
    error: false,
    promptTokens: 100,
    completionTokens: 80,
    model: 'qwen3',
    provider: 'ollama',
    ...overrides,
  };
}

test('healthy agent: low error rate, fast latency, healthy status', () => {
  const m = new HealthMonitor();
  for (let i = 0; i < 50; i++) {
    m.ingest(sample({ latencyMs: 100 + i, timestamp: T0 + i * 1000 }));
  }
  const s = m.snapshot('pm', 300);
  assert.equal(s.requests, 50);
  assert.equal(s.errors, 0);
  assert.equal(s.errorRate, 0);
  assert.ok(s.p50LatencyMs >= 124 && s.p50LatencyMs <= 125, `p50=${s.p50LatencyMs}`);
  assert.ok(s.p95LatencyMs >= 146 && s.p95LatencyMs <= 148, `p95=${s.p95LatencyMs}`);
  assert.equal(s.status, 'healthy');
  assert.ok(s.score > 95);
  assert.equal(s.workspaceId, 'core');
  assert.equal(s.model, 'qwen3');
});

test('degraded and critical on error-rate thresholds', () => {
  const m = new HealthMonitor();
  for (let i = 0; i < 10; i++) m.ingest(sample({ error: i < 2, timestamp: T0 + i }));
  const degraded = m.snapshot('pm', 300);
  assert.equal(degraded.errorRate, 0.2);
  assert.equal(degraded.status, 'degraded');

  for (let i = 10; i < 20; i++) m.ingest(sample({ error: true, timestamp: T0 + i }));
  const critical = m.snapshot('pm', 300);
  assert.equal(critical.errorRate, 0.6);
  assert.equal(critical.status, 'critical');
  assert.ok(critical.score < 50);
});

test('unknown agents and token throughput', () => {
  const m = new HealthMonitor();
  for (let i = 0; i < 10; i++) m.ingest(sample({ timestamp: T0 + i * 6000 }));
  const unknown = m.snapshot('nobody', 300);
  assert.equal(unknown.status, 'unknown');
  assert.equal(unknown.requests, 0);

  const s = m.snapshot('pm', 300); // 10 samples over 54s window
  assert.equal(s.tokensPerMinute > 0, true);
});

test('prune drops stale samples', () => {
  const m = new HealthMonitor();
  m.ingest(sample({ timestamp: T0 - 3_600_000 }));
  m.ingest(sample({ timestamp: T0 }));
  const removed = m.prune(T0 - 60_000);
  assert.equal(removed, 1);
  assert.equal(m.snapshot('pm', 300).requests, 1);
});

test('all() returns a snapshot per active agent', () => {
  const m = new HealthMonitor();
  m.ingest(sample({ agentId: 'pm', timestamp: T0 }));
  m.ingest(sample({ agentId: 'coder', timestamp: T0 }));
  const snaps = m.all(300);
  assert.deepEqual(snaps.map((s) => s.agentId).sort(), ['coder', 'pm']);
});
