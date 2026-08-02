/**
 * Shared types for the EyWALink AIOps toolkit.
 */

/** A single observed request/agent interaction. */
export interface HealthSample {
  agentId: string;
  workspaceId?: string;
  timestamp: number; // epoch ms
  latencyMs: number;
  error: boolean;
  promptTokens: number;
  completionTokens: number;
  model?: string;
  provider?: string;
}

export type AgentStatus = 'healthy' | 'degraded' | 'critical' | 'unknown';

/** Aggregated health of one agent over a window. */
export interface HealthSnapshot {
  agentId: string;
  workspaceId?: string;
  model?: string;
  provider?: string;
  status: AgentStatus;
  windowSeconds: number;
  requests: number;
  errors: number;
  errorRate: number; // 0..1
  p50LatencyMs: number;
  p95LatencyMs: number;
  tokensPerMinute: number;
  score: number; // 0..100, higher = healthier
  updatedAt: number;
}

export type AlertSeverity = 'info' | 'warning' | 'critical';

export type AlertMetric =
  'error_rate' | 'p95_latency_ms' | 'tokens_per_minute' | 'request_rate' | 'cost_per_hour';

export interface AlertRule {
  id: string;
  name: string;
  metric: AlertMetric;
  op: '>' | '<' | '>=' | '<=';
  threshold: number;
  severity: AlertSeverity;
  /** Evaluate against windows of this many seconds (default 60). */
  windowSeconds?: number;
  /** Minimum seconds between firings of this rule for the same scope. */
  cooldownSeconds?: number;
  /** Scope filter — empty means fleet-wide. */
  scope?: {
    agentId?: string;
    workspaceId?: string;
  };
}

export interface AlertEvent {
  id: string;
  ruleId: string;
  ruleName: string;
  agentId: string;
  workspaceId?: string;
  severity: AlertSeverity;
  metric: AlertMetric;
  value: number;
  threshold: number;
  message: string;
  firedAt: number;
  status: 'firing' | 'acknowledged' | 'resolved';
  acknowledgedAt?: number;
  resolvedAt?: number;
}

export type IncidentStatus = 'open' | 'acknowledged' | 'resolved';

/** A set of related alerts grouped into an incident for response workflows. */
export interface Incident {
  id: string;
  title: string;
  severity: AlertSeverity;
  status: IncidentStatus;
  agentIds: string[];
  workspaceIds: string[];
  alertIds: string[];
  openedAt: number;
  acknowledgedAt?: number;
  resolvedAt?: number;
}

/** Token usage converted to USD cost. */
export interface CostEvent {
  agentId: string;
  workspaceId?: string;
  timestamp: number;
  model: string;
  promptTokens: number;
  completionTokens: number;
  costUsd: number;
}

/** Pricing per model: USD per 1M tokens. */
export interface ModelPricing {
  promptPerM: number;
  completionPerM: number;
}

export type BudgetPeriod = 'hour' | 'day' | 'month';

/** A spend limit scoped to an agent or workspace. */
export interface Budget {
  id: string;
  name: string;
  scope?: { agentId?: string; workspaceId?: string };
  period: BudgetPeriod;
  limitUsd: number;
  /** Fire a budget alert when spend reaches this fraction of the limit (0..1). */
  alertAtPct?: number;
}

export interface BudgetAlert {
  budgetId: string;
  name: string;
  scopeLabel: string;
  period: BudgetPeriod;
  limitUsd: number;
  spentUsd: number;
  pctUsed: number; // 0..1+
  firedAt: number;
}

/** Result of model degradation detection for one agent+model pair. */
export interface DegradationReport {
  agentId: string;
  model: string;
  detectedAt: number;
  status: 'degraded' | 'retrain_triggered' | 'healthy';
  severity: AlertSeverity;
  reason: string;
  /** Current window metrics (error_rate, p95_latency_ms, ...). */
  current: Record<string, number>;
  /** Baseline metrics the current window is compared against. */
  baseline: Record<string, number>;
  /** Relative change (current - baseline) / baseline, per metric. */
  delta: Record<string, number>;
  retrainTriggered: boolean;
}
