import { beforeEach, describe, expect, it } from 'vitest';
import type { Alert, VehicleState } from '../api/types';
import { EVENT_BUFFER, pushEvent, reduceMessage, useFleetStore, type LiveEvent } from './store';

const base: VehicleState = {
  vin: 'VIN1',
  tenant_id: 1,
  ts: '2026-10-01T10:00:00.000Z',
  seq: 1,
  lat: 13,
  lon: 80,
  speed_kmh: 30,
  soc_pct: 50,
  soh_pct: 95,
  odo_km: 100,
  charging_state: 'NONE',
  charging_kw: 0,
  dtc: [],
  status: 'DRIVING',
};

const empty = { vehicles: {}, lastApplied: {}, events: [] as LiveEvent[], summary: null };

describe('fleet store reducer', () => {
  it('inserts a new vehicle state', () => {
    const out = reduceMessage(empty, { type: 'state', data: base }, 1000);
    expect(out.vehicles?.VIN1).toEqual(base);
    expect(out.lastApplied?.VIN1).toBe(1000);
    expect(out.events).toBeUndefined();
  });

  it('throttles updates to one per vehicle per 2 s unless status changes', () => {
    const s1 = {
      ...empty,
      ...reduceMessage(empty, { type: 'state', data: base }, 1000),
    } as typeof empty;
    const soon = { ...base, seq: 2, soc_pct: 49, ts: '2026-10-01T10:00:01.000Z' };
    expect(reduceMessage(s1, { type: 'state', data: soon }, 1500)).toEqual({});
    const later = reduceMessage(s1, { type: 'state', data: soon }, 3100);
    expect(later.vehicles?.VIN1?.soc_pct).toBe(49);
    const risk = { ...soon, status: 'AT_RISK' as const };
    const out = reduceMessage(s1, { type: 'state', data: risk }, 1200);
    expect(out.vehicles?.VIN1?.status).toBe('AT_RISK');
    expect(out.events?.[0]).toMatchObject({
      kind: 'state',
      from: 'DRIVING',
      to: 'AT_RISK',
      vin: 'VIN1',
    });
  });

  it('drops out-of-order states', () => {
    const s1 = {
      ...empty,
      ...reduceMessage(empty, { type: 'state', data: { ...base, seq: 10 } }, 1000),
    } as typeof empty;
    const old = { ...base, seq: 9, soc_pct: 1 };
    expect(reduceMessage(s1, { type: 'state', data: old }, 5000)).toEqual({});
  });

  it('keeps a ring buffer of the newest 200 events, newest first, de-duplicated', () => {
    let events: LiveEvent[] = [];
    for (let i = 0; i < 250; i++) {
      const a: Alert = {
        id: i,
        vin: 'V',
        type: 'LOW_SOC',
        severity: 'WARN',
        ts: `t${i}`,
        payload: {},
        acked_by: null,
        acked_ts: null,
      };
      events = pushEvent(events, { kind: 'alert', id: `a:${i}`, ts: a.ts, alert: a });
    }
    expect(events).toHaveLength(EVENT_BUFFER);
    expect(events[0]?.id).toBe('a:249');
    expect(events[EVENT_BUFFER - 1]?.id).toBe('a:50');
    expect(pushEvent(events, events[0]!)).toBe(events);
  });

  it('stores summaries and plan events', () => {
    const sum = reduceMessage(
      empty,
      {
        type: 'summary',
        data: {
          vehicles: 1,
          online: 1,
          charging: 0,
          at_risk: 0,
          avg_soc: 50,
          open_alerts: 0,
          kwh_today: 0,
          cost_today: 0,
          savings_today: 0,
        },
      },
      0,
    );
    expect(sum.summary?.vehicles).toBe(1);
    const plan = reduceMessage(
      empty,
      { type: 'plan', data: { plan_id: 7, vin: 'V', status: 'PLANNED' } },
      0,
    );
    expect(plan.events?.[0]).toMatchObject({ kind: 'plan', plan_id: 7 });
  });
});

describe('fleet store', () => {
  beforeEach(() => useFleetStore.getState().clear());

  it('applies messages and acks alerts in the timeline', () => {
    const a: Alert = {
      id: 5,
      vin: 'VIN1',
      type: 'STRAND_RISK',
      severity: 'CRITICAL',
      ts: 't',
      payload: {},
      acked_by: null,
      acked_ts: null,
    };
    useFleetStore.getState().applyMessage({ type: 'alert', data: a });
    useFleetStore.getState().applyMessage({ type: 'state', data: base });
    expect(useFleetStore.getState().events).toHaveLength(1);
    expect(useFleetStore.getState().vehicles.VIN1?.soc_pct).toBe(50);
    useFleetStore.getState().ackEvent(5, 'me', 'now');
    const ev = useFleetStore.getState().events[0];
    expect(ev?.kind === 'alert' && ev.alert.acked_by).toBe('me');
  });
});
