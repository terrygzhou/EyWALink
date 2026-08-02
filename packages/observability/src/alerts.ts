/**
 * Alert engine and incident response workflow.
 *
 * Rules are evaluated against HealthSnapshots. Firing alerts are deduplicated
 * with a per-rule/scope cooldown, and can be grouped into incidents with an
 * explicit lifecycle: open -> acknowledged -> resolved.
 */

import type { AlertEvent, AlertRule, HealthSnapshot, Incident } from './types.js';

export interface AlertNotifier {
  /** Called when an alert transitions to a new status. */
  notify(alert: AlertEvent): void;
}

let alertSeq = 0;

function metricValue(snapshot: HealthSnapshot, rule: AlertRule): number {
  switch (rule.metric) {
    case 'error_rate':
      return snapshot.errorRate;
    case 'p95_latency_ms':
      return snapshot.p95LatencyMs;
    case 'tokens_per_minute':
      return snapshot.tokensPerMinute;
    case 'request_rate':
      return snapshot.windowSeconds > 0 ? snapshot.requests / snapshot.windowSeconds : 0;
    default:
      return 0;
  }
}

function ruleMatchesScope(rule: AlertRule, snapshot: HealthSnapshot): boolean {
  const scope = rule.scope ?? {};
  if (scope.agentId && scope.agentId !== snapshot.agentId) {
    return false;
  }
  return true;
}

export class AlertEngine {
  private readonly rules: AlertRule[] = [];
  private readonly alerts = new Map<string, AlertEvent>(); // id -> event
  private readonly incidents: Incident[] = [];
  private readonly notifier?: AlertNotifier;

  constructor(notifier?: AlertNotifier) {
    this.notifier = notifier;
  }

  addRule(rule: AlertRule): void {
    this.rules.push(rule);
  }

  getRules(): AlertRule[] {
    return [...this.rules];
  }

  /** Evaluate all rules against a snapshot; returns newly fired alerts. */
  evaluate(snapshot: HealthSnapshot, now = Date.now()): AlertEvent[] {
    const fired: AlertEvent[] = [];
    for (const rule of this.rules) {
      if (!ruleMatchesScope(rule, snapshot)) {
        continue;
      }
      const value = metricValue(snapshot, rule);
      const hit =
        rule.op === '>'
          ? value > rule.threshold
          : rule.op === '<'
            ? value < rule.threshold
            : rule.op === '>='
              ? value >= rule.threshold
              : value <= rule.threshold;
      if (!hit) {
        continue;
      }

      const existing = [...this.alerts.values()].find(
        (a) => a.ruleId === rule.id && a.agentId === snapshot.agentId && a.status !== 'resolved',
      );
      if (existing) {
        // Already firing: refresh value, do not re-fire.
        existing.value = value;
        continue;
      }

      const cooldownMs = (rule.cooldownSeconds ?? 0) * 1000;
      const recentlyResolved = [...this.alerts.values()].find(
        (a) =>
          a.ruleId === rule.id &&
          a.agentId === snapshot.agentId &&
          a.status === 'resolved' &&
          a.resolvedAt !== undefined &&
          now - a.resolvedAt < cooldownMs,
      );
      if (recentlyResolved) {
        continue;
      }

      const alert: AlertEvent = {
        id: `alert-${++alertSeq}`,
        ruleId: rule.id,
        ruleName: rule.name,
        agentId: snapshot.agentId,
        workspaceId: snapshot.workspaceId,
        severity: rule.severity,
        metric: rule.metric,
        value,
        threshold: rule.threshold,
        message: `${rule.name}: ${rule.metric} ${rule.op} ${rule.threshold} (got ${value.toFixed(4)})`,
        firedAt: now,
        status: 'firing',
      };
      this.alerts.set(alert.id, alert);
      fired.push(alert);
      this.notifier?.notify(alert);
    }
    return fired;
  }

  acknowledge(alertId: string, _by = 'agent', at = Date.now()): AlertEvent | undefined {
    const alert = this.alerts.get(alertId);
    if (!alert || alert.status === 'resolved') {
      return undefined;
    }
    alert.status = 'acknowledged';
    alert.acknowledgedAt = at;
    this.notifier?.notify(alert);
    return alert;
  }

  resolve(alertId: string, at = Date.now()): AlertEvent | undefined {
    const alert = this.alerts.get(alertId);
    if (!alert) {
      return undefined;
    }
    alert.status = 'resolved';
    alert.resolvedAt = at;
    this.notifier?.notify(alert);
    return alert;
  }

  getAlerts(): AlertEvent[] {
    return [...this.alerts.values()];
  }

  firingAlerts(): AlertEvent[] {
    return this.getAlerts().filter((a) => a.status === 'firing');
  }

  /**
   * Group all currently-firing alerts into incidents by severity band.
   * Critical alerts open a new incident; open incidents are re-armed when a
   * related alert fires again. Returns incidents created this call.
   */
  syncIncidents(now = Date.now()): Incident[] {
    const firing = this.firingAlerts();
    const created: Incident[] = [];
    const open = this.incidents.filter((i) => i.status !== 'resolved');
    const openAlertIds = new Set(open.flatMap((i) => i.alertIds));

    for (const alert of firing) {
      const existing = open.find((i) => i.alertIds.includes(alert.id));
      if (existing) {
        continue;
      }
      const related = open.find(
        (i) => i.agentIds.includes(alert.agentId) && i.severity === alert.severity,
      );
      if (related) {
        related.alertIds.push(alert.id);
        if (alert.workspaceId && !related.workspaceIds.includes(alert.workspaceId)) {
          related.workspaceIds.push(alert.workspaceId);
        }
        continue;
      }
      const incident: Incident = {
        id: `incident-${now}-${created.length + 1}`,
        title: `${alert.ruleName} on ${alert.agentId}`,
        severity: alert.severity,
        status: 'open',
        agentIds: [alert.agentId],
        workspaceIds: alert.workspaceId ? [alert.workspaceId] : [],
        alertIds: [alert.id],
        openedAt: now,
      };
      this.incidents.push(incident);
      created.push(incident);
      void openAlertIds;
    }
    return created;
  }

  acknowledgeIncident(incidentId: string, by = 'agent', at = Date.now()): Incident | undefined {
    const incident = this.incidents.find((i) => i.id === incidentId);
    if (!incident || incident.status === 'resolved') {
      return undefined;
    }
    incident.status = 'acknowledged';
    incident.acknowledgedAt = at;
    for (const alertId of incident.alertIds) {
      this.acknowledge(alertId, by, at);
    }
    return incident;
  }

  resolveIncident(incidentId: string, at = Date.now()): Incident | undefined {
    const incident = this.incidents.find((i) => i.id === incidentId);
    if (!incident) {
      return undefined;
    }
    incident.status = 'resolved';
    incident.resolvedAt = at;
    for (const alertId of incident.alertIds) {
      this.resolve(alertId, at);
    }
    return incident;
  }

  getIncidents(): Incident[] {
    return [...this.incidents];
  }

  getIncident(id: string): Incident | undefined {
    return this.incidents.find((i) => i.id === id);
  }

  /** Incidents that are still open or acknowledged (dashboard/ops view). */
  active(): Incident[] {
    return this.incidents.filter((i) => i.status !== 'resolved');
  }

  /** All incidents, newest first. */
  all(): Incident[] {
    return [...this.incidents].reverse();
  }
}
