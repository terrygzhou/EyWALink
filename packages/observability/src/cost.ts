/**
 * Cost tracking and budget alerts per agent/workspace.
 *
 * Converts token usage into USD using per-model pricing, then enforces
 * budgets scoped to an agent or workspace with threshold alerts.
 */

import type { Budget, BudgetAlert, BudgetPeriod, CostEvent, ModelPricing } from './types.js';

export interface CostTrackerOptions {
  /** USD per 1M tokens, keyed by model name. */
  pricing?: Record<string, ModelPricing>;
  /** Default pricing when a model is not configured. */
  defaultPricing?: ModelPricing;
}

export const DEFAULT_PRICING: ModelPricing = {
  promptPerM: 0.15,
  completionPerM: 0.6,
};

/** Compute USD cost for a token split under a pricing table. */
export function costForTokens(
  model: string,
  promptTokens: number,
  completionTokens: number,
  pricing: Record<string, ModelPricing>,
  defaultPricing: ModelPricing = DEFAULT_PRICING,
): number {
  const p = pricing[model] ?? defaultPricing;
  return (promptTokens / 1_000_000) * p.promptPerM + (completionTokens / 1_000_000) * p.completionPerM;
}

function scopeMatches(scope: { agentId?: string; workspaceId?: string } | undefined, event: CostEvent): boolean {
  if (!scope) return true;
  if (scope.agentId && scope.agentId !== event.agentId) return false;
  if (scope.workspaceId && scope.workspaceId !== event.workspaceId) return false;
  return true;
}

export class CostTracker {
  private readonly pricing: Record<string, ModelPricing>;
  private readonly defaultPricing: ModelPricing;
  private readonly events: CostEvent[] = [];
  private readonly budgets: Budget[] = [];
  private readonly firedBudgets = new Set<string>();
  private maxEvents: number;

  constructor(options: CostTrackerOptions = {}, maxEvents = 100_000) {
    this.pricing = options.pricing ?? {};
    this.defaultPricing = options.defaultPricing ?? DEFAULT_PRICING;
    this.maxEvents = maxEvents;
  }

  setPricing(model: string, pricing: ModelPricing): void {
    this.pricing[model] = pricing;
  }

  /** Record a token usage event and return the computed cost event. */
  record(
    agentId: string,
    model: string,
    promptTokens: number,
    completionTokens: number,
    opts: { workspaceId?: string; timestamp?: number } = {},
  ): CostEvent {
    const costUsd = costForTokens(model, promptTokens, completionTokens, this.pricing, this.defaultPricing);
    const event: CostEvent = {
      agentId,
      workspaceId: opts.workspaceId,
      timestamp: opts.timestamp ?? Date.now(),
      model,
      promptTokens,
      completionTokens,
      costUsd,
    };
    this.events.push(event);
    if (this.events.length > this.maxEvents) {
      this.events.splice(0, this.events.length - this.maxEvents);
    }
    return event;
  }

  addBudget(budget: Budget): void {
    this.budgets.push(budget);
  }

  /** Spend (USD) for a scope over a period, computed from recorded events. */
  spend(scope: { agentId?: string; workspaceId?: string } | undefined, period: Budget['period'], now = Date.now()): number {
    const start = periodStart(period, now);
    return this.events
      .filter((e) => e.timestamp >= start && scopeMatches(scope, e))
      .reduce((sum, e) => sum + e.costUsd, 0);
  }

  /**
   * Evaluate all budgets; returns new budget alerts when a budget crosses its
   * alert threshold for the first time since the last reset.
   */
  checkBudgets(now = Date.now()): BudgetAlert[] {
    const alerts: BudgetAlert[] = [];
    for (const budget of this.budgets) {
      const spentUsd = this.spend(budget.scope, budget.period, now);
      const pctUsed = budget.limitUsd > 0 ? spentUsd / budget.limitUsd : 0;
      const alertAt = budget.alertAtPct ?? 0.9;
      const key = `${budget.id}:${periodStart(budget.period, now)}`;
      if (pctUsed >= alertAt && !this.firedBudgets.has(key)) {
        this.firedBudgets.add(key);
        alerts.push({
          budgetId: budget.id,
          name: budget.name,
          scopeLabel: scopeLabel(budget.scope),
          period: budget.period,
          limitUsd: budget.limitUsd,
          spentUsd,
          pctUsed,
          firedAt: now,
        });
      }
    }
    return alerts;
  }

  /** Forget fired-budget dedupe state (e.g. at the start of a new period). */
  resetFired(): void {
    this.firedBudgets.clear();
  }

  /**
   * Cost snapshot for a scope (fleet, agent, or workspace) over a period,
   * used by dashboards and budget roll-ups.
   */
  snapshot(
    filter: { agentId?: string; workspaceId?: string } = {},
    period: Budget['period'] = 'day',
    now = Date.now(),
  ): CostSnapshot {
    const start = periodStart(period, now);
    const events = this.events.filter((e) => e.timestamp >= start && scopeMatches(filter, e));
    const scope = filter.agentId || filter.workspaceId ? scopeLabel(filter) : 'fleet';
    return {
      scope,
      period,
      totalCostUsd: events.reduce((sum, e) => sum + e.costUsd, 0),
      inputTokens: events.reduce((sum, e) => sum + e.promptTokens, 0),
      outputTokens: events.reduce((sum, e) => sum + e.completionTokens, 0),
      requests: events.length,
    };
  }

  /** Alias for checkBudgets: returns currently firing budget alerts. */
  evaluateBudgets(now = Date.now()): BudgetAlert[] {
    return this.checkBudgets(now);
  }

  getEvents(): CostEvent[] {
    return [...this.events];
  }
}

/** Aggregated cost view for one scope over a period (see CostTracker.snapshot). */
export interface CostSnapshot {
  scope: string;
  period: BudgetPeriod;
  totalCostUsd: number;
  inputTokens: number;
  outputTokens: number;
  requests: number;
}

function periodStart(period: Budget['period'], now: number): number {
  const d = new Date(now);
  switch (period) {
    case 'hour':
      d.setMinutes(0, 0, 0);
      return d.getTime();
    case 'day':
      d.setHours(0, 0, 0, 0);
      return d.getTime();
    case 'month':
      d.setDate(1);
      d.setHours(0, 0, 0, 0);
      return d.getTime();
  }
}

function scopeLabel(scope: { agentId?: string; workspaceId?: string } | undefined): string {
  if (!scope) return 'fleet';
  if (scope.agentId) return `agent:${scope.agentId}`;
  if (scope.workspaceId) return `workspace:${scope.workspaceId}`;
  return 'fleet';
}
