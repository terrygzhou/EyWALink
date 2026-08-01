/**
 * Model degradation detection and automatic retraining triggers.
 *
 * Maintains a rolling baseline (EWMA) of key metrics per agent+model and
 * flags statistically significant degradation (error-rate or latency drift).
 * When degradation persists past a threshold, emits a retraining trigger.
 */

import type { AlertSeverity, DegradationReport, HealthSnapshot } from './types.js';

export interface DegradationConfig {
  /** EWMA smoothing factor for the baseline (0..1); higher = faster adaptation. */
  alpha?: number;
  /** Relative error-rate increase vs baseline that flags degradation. */
  errorRateDriftFactor?: number;
  /** Absolute error-rate floor before degradation can be declared. */
  errorRateFloor?: number;
  /** Relative p95 latency increase vs baseline that flags degradation. */
  latencyDriftFactor?: number;
  /** Consecutive degraded windows required before retraining triggers. */
  retrainAfterWindows?: number;
}

export interface DegradationNotifier {
  onReport(report: DegradationReport): void;
}

interface ModelState {
  agentId: string;
  model: string;
  errorRateBaseline: number;
  p95LatencyBaseline: number;
  consecutiveDegraded: number;
  totalWindows: number;
  lastStatus: DegradationReport['status'];
  lastSeverity: AlertSeverity;
  lastReason: string;
  retrainTriggeredAt?: number;
}

const DEFAULTS: Required<DegradationConfig> = {
  alpha: 0.2,
  errorRateDriftFactor: 2.0,
  errorRateFloor: 0.05,
  latencyDriftFactor: 2.0,
  retrainAfterWindows: 3,
};

export class DegradationDetector {
  private readonly config: Required<DegradationConfig>;
  private readonly state = new Map<string, ModelState>();
  private readonly notifier?: DegradationNotifier;
  private readonly lastReports = new Map<string, DegradationReport>();

  constructor(config: DegradationConfig = {}, notifier?: DegradationNotifier) {
    this.config = { ...DEFAULTS, ...config };
    this.notifier = notifier;
  }

  /**
   * Feed a health snapshot; returns a report when the model is degraded or
   * when a retraining trigger fires. Returns null when healthy.
   */
  evaluate(snapshot: HealthSnapshot, now = Date.now()): DegradationReport | null {
    if (!snapshot.model || snapshot.requests === 0) return null;
    const key = `${snapshot.agentId}:${snapshot.model}`;
    let state = this.state.get(key);
    if (!state) {
      state = {
        agentId: snapshot.agentId,
        model: snapshot.model,
        errorRateBaseline: snapshot.errorRate,
        p95LatencyBaseline: snapshot.p95LatencyMs || 1,
        consecutiveDegraded: 0,
        totalWindows: 0,
        lastStatus: 'healthy',
        lastSeverity: 'info',
        lastReason: 'baseline established',
      };
      this.state.set(key, state);
    }
    state.totalWindows += 1;

    const alpha = this.config.alpha;
    const baselineErr = state.errorRateBaseline;
    const baselineLat = state.p95LatencyBaseline;

    const errDrift = baselineErr > 0 ? snapshot.errorRate / baselineErr : snapshot.errorRate > 0 ? Infinity : 0;
    const latDrift = baselineLat > 0 ? snapshot.p95LatencyMs / baselineLat : 0;

    const delta: Record<string, number> = {
      error_rate: errDrift,
      p95_latency_ms: latDrift,
    };

    const degraded =
      snapshot.errorRate >= this.config.errorRateFloor &&
      errDrift >= this.config.errorRateDriftFactor;

    let status: DegradationReport['status'] = 'healthy';
    let severity: AlertSeverity = 'info';
    let reason = 'within baseline';

    if (degraded) {
      state.consecutiveDegraded += 1;
      severity = state.consecutiveDegraded >= 2 ? 'critical' : 'warning';
      reason = `error rate ${(errDrift * 100).toFixed(0)}% of baseline (${snapshot.errorRate.toFixed(4)} vs ${baselineErr.toFixed(4)})`;
      status = 'degraded';
      if (state.consecutiveDegraded >= this.config.retrainAfterWindows) {
        status = 'retrain_triggered';
        state.retrainTriggeredAt = now;
        reason += `; retrain triggered after ${state.consecutiveDegraded} degraded windows`;
      }
    } else if (latDrift >= this.config.latencyDriftFactor) {
      state.consecutiveDegraded += 1;
      severity = 'warning';
      reason = `p95 latency ${(latDrift * 100).toFixed(0)}% of baseline (${snapshot.p95LatencyMs.toFixed(0)}ms vs ${baselineLat.toFixed(0)}ms)`;
      status = 'degraded';
    } else {
      state.consecutiveDegraded = 0;
    }

    // Update baselines with EWMA.
    state.errorRateBaseline = alpha * snapshot.errorRate + (1 - alpha) * baselineErr;
    state.p95LatencyBaseline = alpha * (snapshot.p95LatencyMs || baselineLat) + (1 - alpha) * baselineLat;
    state.lastStatus = status;
    state.lastSeverity = severity;
    state.lastReason = reason;

    if (status === 'healthy') {
      // Recovery (or first baseline): clear any previous report for this model.
      this.lastReports.delete(key);
      return null;
    }

    const report: DegradationReport = {
      agentId: snapshot.agentId,
      model: snapshot.model,
      detectedAt: now,
      status,
      severity,
      reason,
      current: {
        error_rate: snapshot.errorRate,
        p95_latency_ms: snapshot.p95LatencyMs,
      },
      baseline: {
        error_rate: baselineErr,
        p95_latency_ms: baselineLat,
      },
      delta,
      retrainTriggered: status === 'retrain_triggered',
    };
    this.lastReports.set(key, report);
    this.notifier?.onReport(report);
    return report;
  }

  /** Latest degradation reports per agent+model (dashboard/ops view). */
  all(): DegradationReport[] {
    return [...this.lastReports.values()];
  }

  /** Get the current model states (for dashboards/debugging). */
  modelStates(): Array<{ agentId: string; model: string; errorRateBaseline: number; p95LatencyBaseline: number; consecutiveDegraded: number }> {
    return [...this.state.values()].map((s) => ({
      agentId: s.agentId,
      model: s.model,
      errorRateBaseline: s.errorRateBaseline,
      p95LatencyBaseline: s.p95LatencyBaseline,
      consecutiveDegraded: s.consecutiveDegraded,
    }));
  }
}
