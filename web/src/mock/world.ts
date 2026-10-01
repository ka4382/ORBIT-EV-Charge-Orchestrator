import type { Alert, AgentAction, AuditRow, Plan, VehicleState, WsMessage } from '../api/types';
import {
  makeAlerts,
  makeAudit,
  makePlans,
  makeVehicles,
  mulberry32,
  summaryFrom,
  type SimVehicle,
} from './data';

/** Shared in-memory world for mock fetch + fake WebSocket. */
class MockWorld {
  vehicles: SimVehicle[] = makeVehicles(10);
  plans: Plan[] = makePlans(this.vehicles.map((v) => v.vehicle.vin));
  alerts: Alert[] = makeAlerts(this.vehicles.map((v) => v.vehicle.vin));
  audit: AuditRow[] = makeAudit();
  actions: AgentAction[] = [];
  private listeners = new Set<(m: WsMessage) => void>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private rng = mulberry32(99);
  private tick = 0;
  private nextId = 900;

  get states(): VehicleState[] {
    return this.vehicles.map((v) => v.state);
  }

  subscribe(fn: (m: WsMessage) => void) {
    this.listeners.add(fn);
    this.start();
    return () => {
      this.listeners.delete(fn);
      if (this.listeners.size === 0) this.stop();
    };
  }

  private emit(m: WsMessage) {
    this.listeners.forEach((l) => l(m));
  }

  start() {
    if (this.timer) return;
    this.timer = setInterval(() => this.step(), 2000);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  step() {
    this.tick += 1;
    for (const v of this.vehicles) {
      const s = v.state;
      if (s.status === 'OFFLINE') continue;
      if (s.status === 'DRIVING' || s.status === 'AT_RISK') {
        v.heading += (this.rng() - 0.5) * 0.6;
        const step = (s.speed_kmh / 3600) * 2 * 1.2; // km in 2 s, sped up
        s.lat += (Math.cos(v.heading) * step) / 111;
        s.lon += (Math.sin(v.heading) * step) / (111 * Math.cos((s.lat * Math.PI) / 180));
        s.soc_pct = Math.max(3, Math.round((s.soc_pct - 0.05) * 10) / 10);
        s.odo_km = Math.round((s.odo_km + step) * 10) / 10;
        s.speed_kmh = Math.max(5, Math.min(70, s.speed_kmh + (this.rng() - 0.5) * 8));
        if (s.soc_pct < 15 && s.status !== 'AT_RISK') {
          s.status = 'AT_RISK';
          this.raise(s.vin, 'LOW_SOC', 'WARN', { soc_pct: s.soc_pct });
        }
      } else if (s.status === 'CHARGING') {
        s.soc_pct = Math.min(100, Math.round((s.soc_pct + 0.3) * 10) / 10);
        if (s.soc_pct >= 100) {
          s.status = 'IDLE';
          s.charging_kw = 0;
          s.charging_state = 'DONE';
        }
      }
      s.ts = new Date().toISOString();
      s.seq += 1;
      this.emit({ type: 'state', data: { ...s, dtc: [...s.dtc] } });
    }
    if (this.tick % 3 === 0) this.emit({ type: 'summary', data: summaryFrom(this.states) });
    if (this.tick % 6 === 0) {
      const v = this.vehicles[Math.floor(this.rng() * this.vehicles.length)]!;
      this.raise(v.state.vin, 'OFF_PLAN', 'INFO', { charger_id: 5, price_per_kwh: 12.5 });
    }
  }

  raise(vin: string, type: string, severity: Alert['severity'], payload: Record<string, unknown>) {
    const a: Alert = {
      id: this.nextId++,
      vin,
      type,
      severity,
      ts: new Date().toISOString(),
      payload,
      acked_by: null,
      acked_ts: null,
    };
    this.alerts.unshift(a);
    this.emit({ type: 'alert', data: a });
    return a;
  }

  ack(id: string | number, by: string) {
    const a = this.alerts.find((x) => String(x.id) === String(id));
    if (!a) return null;
    a.acked_by = by;
    a.acked_ts = new Date().toISOString();
    return a;
  }

  createPlan(vin: string, target_soc: number, depart_ts: string): Plan {
    const template = this.plans[0]!;
    const id = this.nextId++;
    const plan: Plan = {
      ...template,
      id,
      vin,
      target_soc,
      depart_ts,
      status: 'PENDING',
      slots: template.slots.map((s) => ({ ...s })),
    };
    this.plans.unshift(plan);
    setTimeout(() => {
      plan.status = 'PLANNED';
      this.emit({ type: 'plan', data: { plan_id: id, vin, status: 'PLANNED' } });
    }, 1500);
    return plan;
  }

  agentQuery(question: string) {
    const risky = this.vehicles.filter((v) => v.state.status === 'AT_RISK');
    const actions: AgentAction[] = risky.slice(0, 2).map((v, i) => ({
      id: `act-${this.nextId++}`,
      action: i === 0 ? 'dispatch_to_charger' : 'reschedule_charge',
      params:
        i === 0
          ? { vin: v.state.vin, charger_id: 5, eta_min: 14 }
          : { vin: v.state.vin, start: '22:00', target_soc: 85 },
      rationale:
        i === 0
          ? `SoC ${v.state.soc_pct}% with nearest reachable public DC at 6.1 km; drive now before range drops below reach.`
          : `Depot bay 2 is free 22:00-01:00 in the off-peak window; moving this session saves about ₹410.`,
      status: 'PENDING',
    }));
    this.actions.push(...actions);
    const auditId = this.nextId++;
    this.audit.unshift({
      id: auditId,
      ts: new Date().toISOString(),
      actor_type: 'agent',
      actor_id: 'agent',
      action: 'agent.query',
      resource: null,
      details: { question },
    });
    return {
      answer: `${risky.length} vehicle${risky.length === 1 ? '' : 's'} are at risk of not making their next departure: ${risky
        .map((v) => `${v.vehicle.plate} (SoC ${v.state.soc_pct}%)`)
        .join(
          ', ',
        )}. Depot capacity tonight is 4 bays; the off-peak window (22:00-06:00) can absorb all planned sessions with ₹1,140 headroom against today's baseline.`,
      actions,
      audit_id: auditId,
      mode: 'offline' as const,
      usage: { llm_calls: 0, tool_calls: 2, cost_usd: 0, latency_ms: 12 },
    };
  }

  decide(id: string, decision: 'approve' | 'reject') {
    const a = this.actions.find((x) => String(x.id) === id);
    if (!a) return null;
    a.status = decision === 'approve' ? 'APPROVED' : 'REJECTED';
    this.audit.unshift({
      id: this.nextId++,
      ts: new Date().toISOString(),
      actor_type: 'user',
      actor_id: 'manager@fleet',
      action: `agent.action.${decision}`,
      resource: id,
      details: { action: a.action },
    });
    return a;
  }
}

export const world = new MockWorld();
