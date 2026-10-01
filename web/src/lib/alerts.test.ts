import { describe, expect, it } from 'vitest';
import { describeAlert } from './alerts';
import type { Alert } from '../api/types';

const a = (type: string, payload: Record<string, unknown>): Alert =>
  ({
    id: 1,
    vin: 'V',
    type,
    severity: 'WARN',
    ts: '',
    payload,
    acked_by: null,
    acked_ts: null,
  }) as Alert;

// Payload shapes copied from services/alert/orbit_alert/domain/rules.py
describe('describeAlert matches alert-service payloads', () => {
  it('battery DTC lists codes with descriptions', () => {
    expect(
      describeAlert(
        a('BATTERY_DTC', {
          codes: ['P0AFA'],
          descriptions: { P0AFA: 'Battery system voltage low' },
        }),
      ),
    ).toBe('P0AFA · Battery system voltage low');
  });
  it('strand risk shows SoC and remaining range', () => {
    expect(
      describeAlert(
        a('STRAND_RISK', { soc_pct: 11.2, remaining_range_km: 24, nominal_range_km: 220 }),
      ),
    ).toBe('SoC 11.2% · range 24 km · no charger reachable');
  });
  it('SoC drop shows from/to and window', () => {
    expect(
      describeAlert(
        a('SOC_DROP', { from_soc_pct: 81, to_soc_pct: 38, drop_pts: 43, window_s: 7200 }),
      ),
    ).toBe('81% → 38% (−43 pts in 2 h)');
  });
  it('charge interrupted shows previous session', () => {
    expect(
      describeAlert(
        a('CHARGE_INTERRUPTED', { soc_pct: 41, prev_state: 'DC', prev_kw: 48, gap_s: 12 }),
      ),
    ).toBe('DC session stopped at 48 kW · SoC 41%');
  });
});
