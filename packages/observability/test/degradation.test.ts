import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DegradationDetector } from '../src/degradation.ts';
import type { HealthSnapshot } from '../src/types.ts';

const T0 = 1_000_000;

function healthy(): HealthSnapshot {
  return {
    agentId: 'agent-a',
    workspaceId: 'ws-1',
    model: 'qwen3:8b',
    provider: 'ollama',
    status: 'healthy',
    windowSeconds: 60,
    requests: 100,
    errors: 2,
    errorRate: 0.02,
    p50LatencyMs: 250,
    p95LatencyMs: 300,
    tokensPerMinute: 5000,
    score: 100,
    updatedAt: T0,
  };
}

test('no drift while model stays healthy', () => {
  const d = new DegradationDetector();
  assert.equal(d.evaluate(healthy(), T0), null);
  // Baseline now established; a stable window stays quiet.
  assert.equal(d.evaluate(healthy(), T0 + 1000), null);
  assert.equal(d.all().length, 0);
});

test('error-rate spike degrades and persistent drift triggers retraining', () => {
  const reports: string[] = [];
  const d = new DegradationDetector(
    { retrainAfterWindows: 3 },
    { onReport: (r) => reports.push(r.status) },
  );

  // Establish baseline.
  assert.equal(d.evaluate(healthy(), T0), null);

  const degraded = { ...healthy(), errorRate: 0.3, errors: 30, status: 'degraded' as const };
  const r1 = d.evaluate(degraded, T0 + 1000);
  assert.ok(r1 && r1.status === 'degraded' && r1.retrainTriggered === false);
  const r2 = d.evaluate(degraded, T0 + 2000);
  assert.ok(r2 && r2.status === 'degraded');
  const r3 = d.evaluate(degraded, T0 + 3000);
  assert.ok(r3 && r3.status === 'retrain_triggered' && r3.retrainTriggered === true);

  assert.ok(reports.length >= 3);
  assert.ok(reports.includes('retrain_triggered'));
  assert.equal(d.all().length, 1);
  assert.equal(d.all()[0].model, 'qwen3:8b');
});

test('recovery clears the degradation report', () => {
  const d = new DegradationDetector({ retrainAfterWindows: 2 });
  d.evaluate(healthy(), T0);
  d.evaluate({ ...healthy(), errorRate: 0.3, errors: 30 }, T0 + 1000);
  assert.equal(d.all().length, 1);
  assert.equal(d.evaluate(healthy(), T0 + 2000), null);
  assert.equal(d.all().length, 0);
});

test('latency drift alone flags degradation', () => {
  const d = new DegradationDetector();
  d.evaluate(healthy(), T0);
  const r = d.evaluate({ ...healthy(), p95LatencyMs: 3000, status: 'degraded' }, T0 + 1000);
  assert.ok(r && r.status === 'degraded');
  assert.match(r.reason, /latency/);
});

test('insufficient data is ignored', () => {
  const d = new DegradationDetector();
  const noTraffic = { ...healthy(), requests: 0, errorRate: 0 };
  assert.equal(d.evaluate(noTraffic, T0), null);
  const noModel = { ...healthy(), model: undefined };
  assert.equal(d.evaluate(noModel, T0), null);
});
