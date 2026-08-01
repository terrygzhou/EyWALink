/**
 * Agent performance analytics dashboard.
 *
 * Aggregates health, cost, budget, alert, and degradation telemetry into a
 * single snapshot and renders a self-contained HTML dashboard (zero external
 * dependencies — fully offline, no CDN, no lock-in).
 */

import type { HealthSnapshot } from './types.ts';
import type { AlertEvent, Incident } from './types.ts';
import type { BudgetAlert } from './types.ts';
import type { DegradationReport } from './types.ts';

export interface DashboardSnapshot {
  generatedAt: string;
  fleet: {
    agents: number;
    healthy: number;
    degraded: number;
    critical: number;
    unknown: number;
    totalCostUsd: number;
    totalRequests: number;
    totalErrors: number;
  };
  agents: HealthSnapshot[];
  budgets: BudgetAlert[];
  incidents: Incident[];
  firingAlerts: AlertEvent[];
  degradation: Array<Pick<DegradationReport, 'agentId' | 'model' | 'status' | 'severity' | 'reason' | 'retrainTriggered'>>;
}

export interface DashboardSources {
  health: {
    activeAgents(windowSeconds?: number): string[];
    snapshot(agentId: string, windowSeconds?: number): HealthSnapshot;
  };
  cost: {
    /** USD spent for a scope over a period. */
    spend(scope: { agentId?: string; workspaceId?: string } | undefined, period: 'hour' | 'day' | 'month'): number;
    checkBudgets(now?: number): BudgetAlert[];
  };
  alerts: {
    firingAlerts(): AlertEvent[];
    getIncidents(): Incident[];
  };
  degradation: {
    modelStates(): Array<{ agentId: string; model: string; consecutiveDegraded: number }>;
    evaluate(snapshot: HealthSnapshot): DegradationReport | null;
  };
}

export class DashboardBuilder {
  private readonly sources: DashboardSources;
  private readonly windowSeconds: number;

  constructor(sources: DashboardSources, windowSeconds = 300) {
    this.sources = sources;
    this.windowSeconds = windowSeconds;
  }

  snapshot(): DashboardSnapshot {
    const agents = this.sources.health.activeAgents(this.windowSeconds).map((id) =>
      this.sources.health.snapshot(id, this.windowSeconds),
    );
    const fleetCost = this.sources.cost.spend(undefined, 'day');
    const incidents = this.sources.alerts.getIncidents().filter((i) => i.status !== 'resolved');
    const firingAlerts = this.sources.alerts.firingAlerts();

    const degradation = this.sources.degradation
      .modelStates()
      .map((s) => {
        const snap = this.sources.health.snapshot(s.agentId, this.windowSeconds);
        const report = snap.model ? this.sources.degradation.evaluate(snap) : null;
        return report
          ? {
              agentId: report.agentId,
              model: report.model,
              status: report.status,
              severity: report.severity,
              reason: report.reason,
              retrainTriggered: report.retrainTriggered,
            }
          : {
              agentId: s.agentId,
              model: s.model,
              status: 'healthy' as const,
              severity: 'info' as const,
              reason: 'within baseline',
              retrainTriggered: false,
            };
      });

    return {
      generatedAt: new Date().toISOString(),
      fleet: {
        agents: agents.length,
        healthy: agents.filter((a) => a.status === 'healthy').length,
        degraded: agents.filter((a) => a.status === 'degraded').length,
        critical: agents.filter((a) => a.status === 'critical').length,
        unknown: agents.filter((a) => a.status === 'unknown').length,
        totalCostUsd: fleetCost,
        totalRequests: agents.reduce((n, a) => n + a.requests, 0),
        totalErrors: agents.reduce((n, a) => n + a.errors, 0),
      },
      agents,
      budgets: this.sources.cost.checkBudgets(),
      incidents,
      firingAlerts,
      degradation,
    };
  }

  /** Render a standalone HTML dashboard (no external assets). */
  renderHtml(): string {
    const snap = this.snapshot();
    const rows = snap.agents
      .map(
        (a) => `<tr class="${a.status}">
          <td>${esc(a.agentId)}</td>
          <td>${esc(a.workspaceId ?? '—')}</td>
          <td><span class="badge ${a.status}">${a.status}</span></td>
          <td>${(a.errorRate * 100).toFixed(1)}%</td>
          <td>${a.p95LatencyMs.toFixed(0)} ms</td>
          <td>${a.tokensPerMinute.toFixed(0)} tok/min</td>
          <td>${a.score}</td>
        </tr>`,
      )
      .join('\n');

    const incidentRows = snap.incidents
      .map(
        (i) => `<tr><td><span class="badge ${i.severity}">${i.severity}</span></td>
        <td>${esc(i.title)}</td><td>${i.agentIds.join(', ')}</td>
        <td>${new Date(i.openedAt).toISOString()}</td></tr>`,
      )
      .join('\n');

    const alertRows = snap.firingAlerts
      .map(
        (a) => `<tr><td><span class="badge ${a.severity}">${a.severity}</span></td>
        <td>${esc(a.ruleName)}</td><td>${esc(a.agentId)}</td>
        <td>${a.value.toFixed(3)} ${a.metric}</td><td>${esc(a.message)}</td></tr>`,
      )
      .join('\n');

    const budgetRows = snap.budgets
      .map(
        (b) => `<tr><td>${esc(b.name)}</td><td>${esc(b.scopeLabel)}</td>
        <td>$${b.spentUsd.toFixed(2)} / $${b.limitUsd.toFixed(2)}</td>
        <td>${(b.pctUsed * 100).toFixed(0)}%</td></tr>`,
      )
      .join('\n');

    const degRows = snap.degradation
      .map(
        (d) => `<tr><td>${esc(d.agentId)}</td><td>${esc(d.model)}</td>
        <td>${d.retrainTriggered ? '⚠ retrain' : d.status}</td><td>${esc(d.reason)}</td></tr>`,
      )
      .join('\n');

    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>EyWALink AIOps Dashboard</title>
<style>
:root { --bg:#0f1420; --panel:#1a2233; --text:#dbe4f0; --muted:#8fa0b8; --ok:#2ecc71; --warn:#f39c12; --crit:#e74c3c; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; padding:24px; }
h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:14px; color:var(--muted); margin:24px 0 8px; text-transform:uppercase; letter-spacing:.08em; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-top:16px; }
.card { background:var(--panel); border:1px solid #26324a; border-radius:10px; padding:14px; }
.card .k { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.06em; }
.card .v { font-size:26px; margin-top:6px; }
table { width:100%; border-collapse:collapse; margin-top:8px; background:var(--panel); border-radius:10px; overflow:hidden; }
th,td { text-align:left; padding:8px 12px; border-bottom:1px solid #26324a; font-size:12px; }
th { color:var(--muted); font-weight:600; }
tr.healthy td:first-child, .badge.healthy { color:var(--ok); }
tr.degraded td:first-child, .badge.degraded { color:var(--warn); }
tr.critical td:first-child, .badge.critical { color:var(--crit); }
.badge { font-size:11px; font-weight:700; text-transform:uppercase; }
.badge.warning { color:var(--warn); } .badge.info { color:var(--muted); } .badge.unknown { color:var(--muted); }
.footer { color:var(--muted); font-size:11px; margin-top:24px; }
</style>
</head>
<body>
<h1>EyWALink AIOps Dashboard</h1>
<div class="footer">Generated ${esc(snap.generatedAt)} · Zero lock-in · runs entirely offline</div>
<div class="grid">
  <div class="card"><div class="k">Agents</div><div class="v">${snap.fleet.agents}</div></div>
  <div class="card"><div class="k">Healthy</div><div class="v" style="color:var(--ok)">${snap.fleet.healthy}</div></div>
  <div class="card"><div class="k">Degraded</div><div class="v" style="color:var(--warn)">${snap.fleet.degraded}</div></div>
  <div class="card"><div class="k">Critical</div><div class="v" style="color:var(--crit)">${snap.fleet.critical}</div></div>
  <div class="card"><div class="k">Requests</div><div class="v">${snap.fleet.totalRequests}</div></div>
  <div class="card"><div class="k">Errors</div><div class="v">${snap.fleet.totalErrors}</div></div>
  <div class="card"><div class="k">Cost / day</div><div class="v">$${snap.fleet.totalCostUsd.toFixed(2)}</div></div>
  <div class="card"><div class="k">Active incidents</div><div class="v">${snap.incidents.length}</div></div>
</div>
<h2>Agent health</h2>
<table><thead><tr><th>Agent</th><th>Workspace</th><th>Status</th><th>Error rate</th><th>p95 latency</th><th>Throughput</th><th>Score</th></tr></thead>
<tbody>${rows || '<tr><td colspan="7">No agent telemetry yet</td></tr>'}</tbody></table>
<h2>Firing alerts</h2>
<table><thead><tr><th>Severity</th><th>Rule</th><th>Agent</th><th>Value</th><th>Message</th></tr></thead>
<tbody>${alertRows || '<tr><td colspan="5">No firing alerts</td></tr>'}</tbody></table>
<h2>Active incidents</h2>
<table><thead><tr><th>Severity</th><th>Title</th><th>Agents</th><th>Opened</th></tr></thead>
<tbody>${incidentRows || '<tr><td colspan="4">No active incidents</td></tr>'}</tbody></table>
<h2>Budget alerts</h2>
<table><thead><tr><th>Budget</th><th>Scope</th><th>Spend</th><th>Usage</th></tr></thead>
<tbody>${budgetRows || '<tr><td colspan="4">No budget alerts</td></tr>'}</tbody></table>
<h2>Model degradation</h2>
<table><thead><tr><th>Agent</th><th>Model</th><th>Status</th><th>Reason</th></tr></thead>
<tbody>${degRows || '<tr><td colspan="4">No degradation data</td></tr>'}</tbody></table>
</body></html>`;
  }
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
