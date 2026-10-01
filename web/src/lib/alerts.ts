import type { Alert } from '../api/types';

export const alertTitles: Record<string, string> = {
  STRAND_RISK: 'Stranding risk',
  LOW_SOC: 'Low SoC',
  SOC_DROP: 'SoC drop',
  SOH_DROP: 'SoH drop',
  BATTERY_DTC: 'Battery DTC',
  CHARGE_INTERRUPTED: 'Charge interrupted',
  OFF_PLAN: 'Off plan',
};

const fmt = (v: unknown, digits = 1): string =>
  typeof v === 'number' ? v.toFixed(digits).replace(/\.0$/, '') : v == null ? '?' : String(v);

/** One-line description built from the payload keys the alert service emits (services/alert rules.py). */
export function describeAlert(a: Alert): string {
  const p = (a.payload ?? {}) as Record<string, unknown>;
  switch (a.type) {
    case 'STRAND_RISK':
      return `SoC ${fmt(p.soc_pct)}% · range ${fmt(p.remaining_range_km)} km · no charger reachable`;
    case 'LOW_SOC':
      return `SoC ${fmt(p.soc_pct)}%`;
    case 'SOC_DROP':
      return `${fmt(p.from_soc_pct)}% → ${fmt(p.to_soc_pct)}% (−${fmt(p.drop_pts)} pts in ${fmt(Number(p.window_s ?? 7200) / 3600, 0)} h)`;
    case 'SOH_DROP':
      return `SoH ${fmt(p.soh_pct)}%`;
    case 'BATTERY_DTC': {
      const codes = Array.isArray(p.codes) ? (p.codes as string[]) : [];
      const desc = (p.descriptions ?? {}) as Record<string, string>;
      return codes.length
        ? codes.map((c) => (desc[c] ? `${c} · ${desc[c]}` : c)).join(', ')
        : 'Battery fault code';
    }
    case 'CHARGE_INTERRUPTED':
      return `${fmt(p.prev_state)} session stopped at ${fmt(p.prev_kw)} kW · SoC ${fmt(p.soc_pct)}%`;
    case 'OFF_PLAN':
      return `Charging at ₹${fmt(p.price_per_kwh)}/kWh on charger ${fmt(p.charger_id, 0)}`;
    default:
      return Object.entries(p)
        .map(([k, v]) => `${k}=${String(v)}`)
        .join(' ');
  }
}
