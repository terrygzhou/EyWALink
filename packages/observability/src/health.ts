/**
 * Agent health monitoring.
 *
 * Ingests per-request samples and produces rolling HealthSnapshots with
 * latency percentiles, error rate, token throughput, and a composite score.
 */

import type { AgentStatus, HealthSample, HealthSnapshot } from './types.js';

/** Default window used when a snapshot is requested without one. */
export const DEFAULT_WINDOW_SECONDS = 300;

/** Percentile helper over a sorted numeric array. */
function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  const frac = idx - lo;
  return sorted[lo] * (1 - frac) + sorted[hi] * frac;
}

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

export class HealthMonitor {
  private readonly samples: HealthSample[] = [];

  /** Buffer all samples in memory (ring semantics via prune). */
  private maxSamples: number;

  constructor(maxSamples = 50_000) {
    this.maxSamples = maxSamples;
  }

  /** Record one request sample. */
  ingest(sample: HealthSample): void {
    this.samples.push(sample);
    if (this.samples.length > this.maxSamples) {
      this.samples.splice(0, this.samples.length - this.maxSamples);
    }
  }

  /** Drop samples older than `cutoffMs`. Returns number removed. */
  prune(cutoffMs: number): number {
    const before = this.samples.length;
    let i = 0;
    while (i < this.samples.length && this.samples[i].timestamp < cutoffMs) i++;
    if (i > 0) this.samples.splice(0, i);
    return before - this.samples.length;
  }

  /** Compute a snapshot for one agent over the trailing window. */
  snapshot(agentId: string, windowSeconds = DEFAULT_WINDOW_SECONDS): HealthSnapshot {
    const now = Date.now();
    const cutoff = now - windowSeconds * 1000;
    const window = this.samples.filter((s) => s.agentId === agentId && s.timestamp >= cutoff);

    const updatedAt = window.reduce((max, s) => Math.max(max, s.timestamp), 0);
    const lastSample = window[window.length - 1];
    if (window.length === 0) {
      return {
        agentId,
        workspaceId: undefined,
        status: 'unknown',
        windowSeconds,
        requests: 0,
        errors: 0,
        errorRate: 0,
        p50LatencyMs: 0,
        p95LatencyMs: 0,
        tokensPerMinute: 0,
        score: 100,
        updatedAt,
      };
    }

    const errors = window.filter((s) => s.error).length;
    const errorRate = errors / window.length;
    const latencies = window.map((s) => s.latencyMs).sort((a, b) => a - b);
    const totalTokens = window.reduce((sum, s) => sum + s.promptTokens + s.completionTokens, 0);
    const minutes = Math.max(windowSeconds / 60, 1 / 60);
    const tokensPerMinute = totalTokens / minutes;

    // Composite score: start at 100, penalize errors heavily, latency mildly.
    let score = 100;
    score -= errorRate * 100 * 2; // up to -200 but clamped below
    const p95 = percentile(latencies, 95);
    if (p95 > 10_000) score -= 40;
    else if (p95 > 2_000) score -= 20;
    else if (p95 > 500) score -= 5;
    score = clamp(score, 0, 100);

    const status: AgentStatus = errorRate >= 0.5 ? 'critical' : errorRate >= 0.1 ? 'degraded' : 'healthy';

    return {
      agentId,
      workspaceId: lastSample.workspaceId,
      model: lastSample.model,
      provider: lastSample.provider,
      status,
      windowSeconds,
      requests: window.length,
      errors,
      errorRate,
      p50LatencyMs: percentile(latencies, 50),
      p95LatencyMs: p95,
      tokensPerMinute,
      score: Math.round(score),
      updatedAt,
    };
  }

  /** List agents with any samples in the trailing window. */
  activeAgents(windowSeconds = DEFAULT_WINDOW_SECONDS): string[] {
    const cutoff = Date.now() - windowSeconds * 1000;
    const agents = new Set<string>();
    for (const s of this.samples) if (s.timestamp >= cutoff) agents.add(s.agentId);
    return [...agents];
  }

  /** Snapshots for every agent with samples in the trailing window. */
  all(windowSeconds = DEFAULT_WINDOW_SECONDS): HealthSnapshot[] {
    return this.activeAgents(windowSeconds).map((a) => this.snapshot(a, windowSeconds));
  }
}
