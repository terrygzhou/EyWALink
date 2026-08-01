/**
 * Agent performance analytics.
 *
 * Aggregates raw samples into per-agent and per-workspace performance
 * summaries suitable for a dashboard: request volume, latency percentiles,
 * error rate, token throughput, and cost.
 */

import type { CostEvent, HealthSample } from './types.js';

export interface AgentPerformance {
  agentId: string;
  workspaceId?: string;
  requests: number;
  errors: number;
  errorRate: number;
  p50LatencyMs: number;
  p95LatencyMs: number;
  maxLatencyMs: number;
  promptTokens: number;
  completionTokens: number;
  tokensPerMinute: number;
  costUsd: number;
}

export interface WorkspacePerformance {
  workspaceId: string;
  agents: number;
  requests: number;
  errors: number;
  errorRate: number;
  p95LatencyMs: number;
  tokensPerMinute: number;
  costUsd: number;
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  const frac = idx - lo;
  return sorted[lo] * (1 - frac) + sorted[hi] * frac;
}

export class AnalyticsEngine {
  private samples: HealthSample[] = [];
  private costs: CostEvent[] = [];
  private maxSamples: number;

  constructor(maxSamples = 100_000) {
    this.maxSamples = maxSamples;
  }

  ingest(sample: HealthSample): void {
    this.samples.push(sample);
    if (this.samples.length > this.maxSamples) {
      this.samples.splice(0, this.samples.length - this.maxSamples);
    }
  }

  ingestCost(cost: CostEvent): void {
    this.costs.push(cost);
    if (this.costs.length > this.maxSamples) {
      this.costs.splice(0, this.costs.length - this.maxSamples);
    }
  }

  prune(cutoffMs: number): void {
    this.samples = this.samples.filter((s) => s.timestamp >= cutoffMs);
    this.costs = this.costs.filter((c) => c.timestamp >= cutoffMs);
  }

  /** Per-agent performance over the trailing window. */
  agentPerformance(agentId: string, windowSeconds = 300): AgentPerformance {
    const cutoff = Date.now() - windowSeconds * 1000;
    const window = this.samples.filter((s) => s.agentId === agentId && s.timestamp >= cutoff);
    const latencies = window.map((s) => s.latencyMs).sort((a, b) => a - b);
    const errors = window.filter((s) => s.error).length;
    const promptTokens = window.reduce((sum, s) => sum + s.promptTokens, 0);
    const completionTokens = window.reduce((sum, s) => sum + s.completionTokens, 0);
    const minutes = Math.max(windowSeconds / 60, 1 / 60);
    const costUsd = this.costs
      .filter((c) => c.agentId === agentId && c.timestamp >= cutoff)
      .reduce((sum, c) => sum + c.costUsd, 0);
    const last = window[window.length - 1];

    return {
      agentId,
      workspaceId: last?.workspaceId,
      requests: window.length,
      errors,
      errorRate: window.length ? errors / window.length : 0,
      p50LatencyMs: percentile(latencies, 50),
      p95LatencyMs: percentile(latencies, 95),
      maxLatencyMs: latencies.length ? latencies[latencies.length - 1] : 0,
      promptTokens,
      completionTokens,
      tokensPerMinute: (promptTokens + completionTokens) / minutes,
      costUsd,
    };
  }

  /** All active agents' performance. */
  fleetPerformance(windowSeconds = 300): AgentPerformance[] {
    const cutoff = Date.now() - windowSeconds * 1000;
    const agents = new Set<string>();
    for (const s of this.samples) if (s.timestamp >= cutoff) agents.add(s.agentId);
    return [...agents].map((a) => this.agentPerformance(a, windowSeconds));
  }

  /** Roll-up per workspace over the trailing window. */
  workspacePerformance(windowSeconds = 300): WorkspacePerformance[] {
    const cutoff = Date.now() - windowSeconds * 1000;
    const byWorkspace = new Map<string, HealthSample[]>();
    for (const s of this.samples) {
      if (s.timestamp < cutoff || !s.workspaceId) continue;
      const list = byWorkspace.get(s.workspaceId) ?? [];
      list.push(s);
      byWorkspace.set(s.workspaceId, list);
    }
    const result: WorkspacePerformance[] = [];
    for (const [workspaceId, window] of byWorkspace) {
      const agents = new Set(window.map((s) => s.agentId));
      const errors = window.filter((s) => s.error).length;
      const latencies = window.map((s) => s.latencyMs).sort((a, b) => a - b);
      const minutes = Math.max(windowSeconds / 60, 1 / 60);
      const tokens = window.reduce((sum, s) => sum + s.promptTokens + s.completionTokens, 0);
      const costUsd = this.costs
        .filter((c) => c.workspaceId === workspaceId && c.timestamp >= cutoff)
        .reduce((sum, c) => sum + c.costUsd, 0);
      result.push({
        workspaceId,
        agents: agents.size,
        requests: window.length,
        errors,
        errorRate: window.length ? errors / window.length : 0,
        p95LatencyMs: percentile(latencies, 95),
        tokensPerMinute: tokens / minutes,
        costUsd,
      });
    }
    return result;
  }
}
