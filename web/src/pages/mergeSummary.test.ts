import { describe, expect, it } from 'vitest';
import { mergeSummary } from './FleetConsole';
import type { FleetSummary } from '../api/types';

const base: FleetSummary = {
  vehicles: 10,
  online: 9,
  charging: 1,
  at_risk: 0,
  avg_soc: 50,
  open_alerts: 7,
  kwh_today: 1,
  cost_today: 300,
  savings_today: 120,
};

describe('mergeSummary', () => {
  it('keeps alerts and money from REST and stream counts from the WebSocket', () => {
    const live = {
      ...base,
      vehicles: 12500,
      online: 12400,
      open_alerts: 0,
      cost_today: 0,
      savings_today: 0,
    };
    const m = mergeSummary(base, live)!;
    expect(m.vehicles).toBe(12500);
    expect(m.online).toBe(12400);
    expect(m.open_alerts).toBe(7);
    expect(m.savings_today).toBe(120);
  });
  it('falls back to whichever side exists', () => {
    expect(mergeSummary(null, base)).toBe(base);
    expect(mergeSummary(base, null)).toBe(base);
    expect(mergeSummary(null, null)).toBeNull();
  });
});
