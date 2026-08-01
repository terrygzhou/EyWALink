import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MetricRegistry, serializeLabels } from '../src/metrics.ts';

test('counter increments and renders Prometheus exposition', () => {
  const reg = new MetricRegistry();
  const c = reg.counter('agent_requests_total', 'Total agent requests');
  c.inc({ agent: 'pm' });
  c.inc({ agent: 'pm' });
  c.inc({ agent: 'coder' });
  const out = reg.render();
  assert.match(out, /# HELP agent_requests_total Total agent requests/);
  assert.match(out, /# TYPE agent_requests_total counter/);
  assert.match(out, /agent_requests_total\{agent="pm"\} 2/);
  assert.match(out, /agent_requests_total\{agent="coder"\} 1/);
});

test('gauge set/add/get', () => {
  const reg = new MetricRegistry();
  const g = reg.gauge('inflight', 'In-flight requests');
  g.set(3);
  g.add(2);
  assert.equal(g.get(), 5);
  g.set(1, { agent: 'pm' });
  assert.equal(g.get({ agent: 'pm' }), 1);
});

test('histogram buckets and sums', () => {
  const reg = new MetricRegistry();
  const h = reg.histogram('agent_latency_seconds', 'Agent latency', [0.1, 0.5, 1]);
  h.observe(0.05);
  h.observe(0.4);
  h.observe(2.0);
  const { count, sum } = h.get();
  assert.equal(count, 3);
  assert.ok(Math.abs(sum - 2.45) < 1e-9);
  const out = reg.render();
  assert.match(out, /agent_latency_seconds_bucket\{le="0\.1"\} 1/);
  assert.match(out, /agent_latency_seconds_bucket\{le="0\.5"\} 2/);
  assert.match(out, /agent_latency_seconds_bucket\{le="1"\} 2/);
  assert.match(out, /agent_latency_seconds_bucket\{le="\+Inf"\} 3/);
  assert.match(out, /agent_latency_seconds_count 3/);
});

test('duplicate metric names are rejected', () => {
  const reg = new MetricRegistry();
  reg.counter('x_total', 'x');
  assert.throws(() => reg.counter('x_total', 'x again'));
});

test('serializeLabels is canonical and escapes quotes', () => {
  assert.equal(serializeLabels({ b: '2', a: '1' }), 'a="1",b="2"');
  assert.equal(serializeLabels({ k: 'va"lue' }), 'k="va"lue"');
  assert.equal(serializeLabels({}), '');
});
