import { create } from 'zustand';
import type { Alert, FleetSummary, VehicleState, WsMessage } from '../api/types';

export const EVENT_BUFFER = 200;
export const STATE_THROTTLE_MS = 2000;

export type LiveEvent =
  | { kind: 'alert'; id: string; ts: string; alert: Alert }
  | {
      kind: 'state';
      id: string;
      ts: string;
      vin: string;
      from: VehicleState['status'] | null;
      to: VehicleState['status'];
      soc: number;
    }
  | { kind: 'plan'; id: string; ts: string; vin: string; plan_id: string | number; status: string };

export type WsStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface FleetStore {
  vehicles: Record<string, VehicleState>;
  lastApplied: Record<string, number>;
  events: LiveEvent[];
  summary: FleetSummary | null;
  status: WsStatus;
  version: number;
  applyMessage: (m: WsMessage, now?: number) => void;
  ackEvent: (alertId: string | number, by: string, ts: string) => void;
  setStatus: (s: WsStatus) => void;
  seedVehicles: (states: VehicleState[]) => void;
  clear: () => void;
}

/**
 * Pure reducer over the store slice; exported for tests.
 * - state: replaced only if newer (ts/seq) and at most once per vehicle per 2 s
 *   (server also throttles; client guards against bursts on reconnect).
 * - status transitions become timeline events.
 * - events ring buffer keeps the newest 200 (newest first).
 */
export function reduceMessage(
  s: Pick<FleetStore, 'vehicles' | 'lastApplied' | 'events' | 'summary'>,
  m: WsMessage,
  now: number,
): Partial<Pick<FleetStore, 'vehicles' | 'lastApplied' | 'events' | 'summary'>> {
  switch (m.type) {
    case 'state': {
      const st = m.data;
      const prev = s.vehicles[st.vin];
      const last = s.lastApplied[st.vin] ?? 0;
      const statusChanged = !!prev && prev.status !== st.status;
      if (prev && !statusChanged && now - last < STATE_THROTTLE_MS) return {};
      if (prev && (prev.seq > st.seq || Date.parse(prev.ts) > Date.parse(st.ts))) return {};
      const out: Partial<Pick<FleetStore, 'vehicles' | 'lastApplied' | 'events'>> = {
        vehicles: { ...s.vehicles, [st.vin]: st },
        lastApplied: { ...s.lastApplied, [st.vin]: now },
      };
      if (statusChanged) {
        const ev: LiveEvent = {
          kind: 'state',
          id: `s:${st.vin}:${st.seq}`,
          ts: st.ts,
          vin: st.vin,
          from: prev.status,
          to: st.status,
          soc: st.soc_pct,
        };
        out.events = pushEvent(s.events, ev);
      }
      return out;
    }
    case 'alert': {
      const a = m.data;
      const ev: LiveEvent = { kind: 'alert', id: `a:${a.id}`, ts: a.ts, alert: a };
      return { events: pushEvent(s.events, ev) };
    }
    case 'plan': {
      const ev: LiveEvent = {
        kind: 'plan',
        id: `p:${m.data.plan_id}:${m.data.status}`,
        ts: new Date(now).toISOString(),
        vin: m.data.vin,
        plan_id: m.data.plan_id,
        status: m.data.status,
      };
      return { events: pushEvent(s.events, ev) };
    }
    case 'summary':
      return { summary: m.data };
    default:
      return {};
  }
}

export function pushEvent(events: LiveEvent[], ev: LiveEvent): LiveEvent[] {
  if (events.some((e) => e.id === ev.id)) return events;
  const next = [ev, ...events];
  return next.length > EVENT_BUFFER ? next.slice(0, EVENT_BUFFER) : next;
}

export const useFleetStore = create<FleetStore>((set) => ({
  vehicles: {},
  lastApplied: {},
  events: [],
  summary: null,
  status: 'idle',
  version: 0,
  applyMessage: (m, now = Date.now()) =>
    set((s) => {
      const patch = reduceMessage(s, m, now);
      if (Object.keys(patch).length === 0) return {};
      return { ...patch, version: s.version + 1 };
    }),
  ackEvent: (alertId, by, ts) =>
    set((s) => ({
      events: s.events.map((e) =>
        e.kind === 'alert' && String(e.alert.id) === String(alertId)
          ? { ...e, alert: { ...e.alert, acked_by: by, acked_ts: ts } }
          : e,
      ),
    })),
  setStatus: (status) => set({ status }),
  seedVehicles: (states) =>
    set((s) => {
      const vehicles = { ...s.vehicles };
      for (const st of states) if (!vehicles[st.vin]) vehicles[st.vin] = st;
      return { vehicles, version: s.version + 1 };
    }),
  clear: () => set({ vehicles: {}, lastApplied: {}, events: [], summary: null, version: 0 }),
}));
