/**
 * @module @eywalink/observability
 * AIOps toolkit for monitoring and maintaining AI agent fleets.
 *
 * Zero-dependency, zero lock-in: plain TypeScript that runs on stock Node,
 * emits Prometheus text exposition, and renders a self-contained HTML
 * dashboard. Pair with the Grafana/Prometheus provisioning files under
 * `grafana/` and `prometheus/` when running the full reference stack.
 */

export * from './types.ts';
export * from './metrics.ts';
export * from './health.ts';
export * from './alerts.ts';
export * from './analytics.ts';
export * from './cost.ts';
export * from './degradation.ts';
export * from './dashboard.ts';

import { MetricRegistry } from './metrics.ts';
import { HealthMonitor } from './health.ts';
import { AnalyticsEngine } from './analytics.ts';
import { AlertEngine } from './alerts.ts';
import { CostTracker } from './cost.ts';
import { DegradationDetector } from './degradation.ts';
import { DashboardBuilder } from './dashboard.ts';
import type { DashboardSources } from './dashboard.ts';

export interface AIOpsBundle {
  metrics: MetricRegistry;
  health: HealthMonitor;
  alerts: AlertEngine;
  analytics: AnalyticsEngine;
  cost: CostTracker;
  degradation: DegradationDetector;
  dashboard: DashboardBuilder;
}

/**
 * Create a wired-up AIOps bundle: one object that owns every subsystem.
 * Usage:
 *   const aiops = createAIOps();
 *   aiops.health.ingest({ agentId: 'pm', latencyMs: 320, error: false, promptTokens: 100, completionTokens: 50, timestamp: Date.now() });
 *   const html = aiops.dashboard.renderHtml();
 */
export function createAIOps(): AIOpsBundle {
  const metrics = new MetricRegistry();
  const health = new HealthMonitor();
  const analytics = new AnalyticsEngine();
  const alerts = new AlertEngine();
  const cost = new CostTracker();
  const degradation = new DegradationDetector();
  const sources: DashboardSources = { health, cost, alerts, degradation };
  const dashboard = new DashboardBuilder(sources);

  return { metrics, health, analytics, alerts, cost, degradation, dashboard };
}
