import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CostTracker, costForTokens, DEFAULT_PRICING } from '../src/cost.ts';

const T0 = Date.now();

test('cost derived from pricing table per model', () => {
  const t = new CostTracker({ pricing: { qwen3: { promptPerM: 0.15, completionPerM: 0.6 } } });
  t.record('pm', 'qwen3', 1_000_000, 1_000_000, { workspaceId: 'core', timestamp: T0 });
  assert.equal(t.spend(undefined, 'day', T0), 0.75);
  assert.equal(
    costForTokens('qwen3', 1_000_000, 1_000_000, { qwen3: { promptPerM: 0.15, completionPerM: 0.6 } }),
    0.75,
  );
  assert.ok(DEFAULT_PRICING.promptPerM > 0);
});

test('unpriced models fall back to default pricing', () => {
  const t = new CostTracker();
  t.record('pm', 'local-qwen', 1_000_000, 0, { timestamp: T0 });
  assert.equal(t.spend(undefined, 'day', T0), DEFAULT_PRICING.promptPerM);
});

test('snapshot aggregates tokens and cost per scope', () => {
  const t = new CostTracker({ pricing: { qwen3: { promptPerM: 0.15, completionPerM: 0.6 } } });
  t.record('pm', 'qwen3', 100_000, 50_000, { workspaceId: 'core', timestamp: T0 });
  t.record('coder', 'qwen3', 100_000, 50_000, { workspaceId: 'core', timestamp: T0 });

  const fleet = t.snapshot();
  assert.equal(fleet.scope, 'fleet');
  assert.equal(fleet.requests, 2);
  assert.equal(fleet.inputTokens, 200_000);
  assert.equal(fleet.outputTokens, 100_000);
  assert.ok(Math.abs(fleet.totalCostUsd - 0.09) < 1e-9); // 200k*0.15/1M + 100k*0.6/1M

  const pm = t.snapshot({ agentId: 'pm' });
  assert.equal(pm.scope, 'agent:pm');
  assert.equal(pm.inputTokens, 100_000);
});

test('budget alerts fire once per period and respect scope', () => {
  const t = new CostTracker({ pricing: { qwen3: { promptPerM: 0.15, completionPerM: 0.6 } } });
  t.record('pm', 'qwen3', 1_000_000, 0, { workspaceId: 'core', timestamp: T0 });

  t.addBudget({
    id: 'b1',
    name: 'core daily',
    scope: { workspaceId: 'core' },
    period: 'day',
    limitUsd: 0.10,
    alertAtPct: 1.0,
  });
  const alerts = t.checkBudgets(T0);
  assert.equal(alerts.length, 1);
  assert.equal(alerts[0].budgetId, 'b1');
  assert.ok(Math.abs(alerts[0].pctUsed - 1.5) < 1e-9);

  // Same period: no duplicate alert.
  assert.equal(t.checkBudgets(T0 + 1000).length, 0);

  // evaluateBudgets is an alias for the dashboard path.
  assert.equal(t.evaluateBudgets(T0 + 1000).length, 0);
});

test('scoped budget does not count other agents', () => {
  const t = new CostTracker({ pricing: { qwen3: { promptPerM: 0.15, completionPerM: 0.6 } } });
  t.record('pm', 'qwen3', 1_000_000, 0, { workspaceId: 'core', timestamp: T0 });
  t.addBudget({
    id: 'b2',
    name: 'coder only',
    scope: { agentId: 'coder' },
    period: 'day',
    limitUsd: 1,
    alertAtPct: 1.0,
  });
  assert.equal(t.checkBudgets(T0).length, 0);
});
