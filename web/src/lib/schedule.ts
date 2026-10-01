import type { PlanSlot, Tariff, TariffSlot } from '../api/types';

export const SLOT_MIN = 15;
export const DAY_MIN = 1440;

export type Band = 'offpeak' | 'normal' | 'peak';

export interface MergedRun {
  charger_id: number;
  start_ts: string;
  end_ts: string;
  startMin: number;
  endMin: number;
  kw: number;
  kwh: number;
  cost: number;
  price_per_kwh: number;
  band: Band;
  slots: number;
}

export function minuteOfDay(ts: string | Date, dayStart?: Date): number {
  const d = new Date(ts);
  if (dayStart) return Math.round((d.getTime() - dayStart.getTime()) / 60_000);
  return d.getHours() * 60 + d.getMinutes();
}

export function startOfToday(now = new Date()): Date {
  const d = new Date(now);
  d.setHours(0, 0, 0, 0);
  return d;
}

/** Classify a tariff price into a band relative to the tariff's own price range. */
export function bandFor(price: number, prices: number[]): Band {
  const uniq = Array.from(new Set(prices)).sort((a, b) => a - b);
  if (uniq.length <= 1) return 'normal';
  const min = uniq[0]!;
  const max = uniq[uniq.length - 1]!;
  if (price <= min) return 'offpeak';
  if (price >= max) return 'peak';
  return 'normal';
}

/**
 * Merge contiguous 15-minute slots on the same charger with the same power and
 * price into runs. Slots are sorted first; a gap or a change in charger / kw /
 * price starts a new run. `dayStart` anchors minute offsets for the 24 h axis.
 */
export function mergeSlots(slots: PlanSlot[], dayStart: Date = startOfToday()): MergedRun[] {
  const prices = slots.map((s) => s.price_per_kwh);
  const sorted = [...slots].sort(
    (a, b) => a.charger_id - b.charger_id || Date.parse(a.start_ts) - Date.parse(b.start_ts),
  );
  const runs: MergedRun[] = [];
  for (const s of sorted) {
    const last = runs[runs.length - 1];
    const startMin = minuteOfDay(s.start_ts, dayStart);
    const endMin = minuteOfDay(s.end_ts, dayStart);
    if (
      last &&
      last.charger_id === s.charger_id &&
      last.endMin === startMin &&
      last.kw === s.kw &&
      last.price_per_kwh === s.price_per_kwh
    ) {
      last.end_ts = s.end_ts;
      last.endMin = endMin;
      last.kwh += s.kwh;
      last.cost += s.cost;
      last.slots += 1;
    } else {
      runs.push({
        charger_id: s.charger_id,
        start_ts: s.start_ts,
        end_ts: s.end_ts,
        startMin,
        endMin,
        kw: s.kw,
        kwh: s.kwh,
        cost: s.cost,
        price_per_kwh: s.price_per_kwh,
        band: bandFor(s.price_per_kwh, prices),
        slots: 1,
      });
    }
  }
  for (const r of runs) {
    r.kwh = Math.round(r.kwh * 100) / 100;
    r.cost = Math.round(r.cost * 100) / 100;
  }
  return runs;
}

export interface TariffBand {
  startMin: number;
  endMin: number;
  price: number;
  band: Band;
}

/** Tariff bands for one weekday, merged where contiguous with the same price. */
export function tariffBands(tariff: Tariff | undefined, dow: number): TariffBand[] {
  if (!tariff) return [];
  const prices = tariff.slots.map((s) => s.price_per_kwh);
  const day: TariffSlot[] = tariff.slots
    .filter((s) => s.dow === dow)
    .sort((a, b) => a.start_min - b.start_min);
  const out: TariffBand[] = [];
  for (const s of day) {
    const last = out[out.length - 1];
    if (last && last.endMin === s.start_min && last.price === s.price_per_kwh) {
      last.endMin = s.end_min;
    } else {
      out.push({
        startMin: s.start_min,
        endMin: s.end_min,
        price: s.price_per_kwh,
        band: bandFor(s.price_per_kwh, prices),
      });
    }
  }
  return out;
}

/** Sum kW across all runs per 15-min bucket to draw the depot capacity line. */
export function loadProfile(runs: MergedRun[], buckets = DAY_MIN / SLOT_MIN): number[] {
  const out = new Array<number>(buckets).fill(0);
  for (const r of runs) {
    const a = Math.max(0, Math.floor(r.startMin / SLOT_MIN));
    const b = Math.min(buckets, Math.ceil(r.endMin / SLOT_MIN));
    for (let i = a; i < b; i++) out[i] = (out[i] ?? 0) + r.kw;
  }
  return out;
}
