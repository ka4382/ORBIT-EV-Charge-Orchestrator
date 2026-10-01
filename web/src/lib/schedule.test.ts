import { describe, expect, it } from 'vitest';
import type { PlanSlot, Tariff } from '../api/types';
import { bandFor, loadProfile, mergeSlots, tariffBands } from './schedule';

const day = new Date('2026-10-01T00:00:00');
function slot(charger: number, h: number, m: number, kw: number, price: number): PlanSlot {
  const s = new Date(day);
  s.setHours(h, m, 0, 0);
  const e = new Date(s.getTime() + 15 * 60_000);
  return {
    charger_id: charger,
    start_ts: s.toISOString(),
    end_ts: e.toISOString(),
    kw,
    kwh: kw / 4,
    cost: (kw / 4) * price,
    price_per_kwh: price,
  };
}

describe('mergeSlots', () => {
  it('merges contiguous slots with the same charger, kw and price', () => {
    const slots = [slot(1, 1, 0, 60, 5.5), slot(1, 1, 15, 60, 5.5), slot(1, 1, 30, 60, 5.5)];
    const runs = mergeSlots(slots, day);
    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({
      charger_id: 1,
      startMin: 60,
      endMin: 105,
      kwh: 45,
      cost: 247.5,
      slots: 3,
      band: 'normal',
    });
  });

  it('splits on gaps, charger, power or price changes and sorts input', () => {
    const slots = [
      slot(1, 2, 0, 60, 5.5),
      slot(1, 1, 0, 60, 5.5), // out of order, gap before 02:00
      slot(1, 2, 15, 22, 5.5), // kw change
      slot(1, 2, 30, 22, 12.5), // price change
      slot(2, 2, 45, 22, 12.5), // charger change
    ];
    const runs = mergeSlots(slots, day);
    expect(runs.map((r) => [r.charger_id, r.startMin, r.endMin, r.kw, r.band])).toEqual([
      [1, 60, 75, 60, 'offpeak'],
      [1, 120, 135, 60, 'offpeak'],
      [1, 135, 150, 22, 'offpeak'],
      [1, 150, 165, 22, 'peak'],
      [2, 165, 180, 22, 'peak'],
    ]);
  });

  it('bandFor classifies against the price range', () => {
    expect(bandFor(5.5, [5.5, 8, 12.5])).toBe('offpeak');
    expect(bandFor(8, [5.5, 8, 12.5])).toBe('normal');
    expect(bandFor(12.5, [5.5, 8, 12.5])).toBe('peak');
    expect(bandFor(8, [8])).toBe('normal');
  });

  it('tariffBands merges contiguous equal-price slots for a weekday', () => {
    const t: Tariff = {
      tariff_id: 1,
      name: 't',
      slots: [
        { dow: 1, start_min: 0, end_min: 180, price_per_kwh: 5 },
        { dow: 1, start_min: 180, end_min: 360, price_per_kwh: 5 },
        { dow: 1, start_min: 360, end_min: 1440, price_per_kwh: 9 },
        { dow: 2, start_min: 0, end_min: 1440, price_per_kwh: 9 },
      ],
    };
    expect(tariffBands(t, 1)).toEqual([
      { startMin: 0, endMin: 360, price: 5, band: 'offpeak' },
      { startMin: 360, endMin: 1440, price: 9, band: 'peak' },
    ]);
    expect(tariffBands(undefined, 1)).toEqual([]);
  });

  it('loadProfile sums kW per 15-min bucket', () => {
    const runs = mergeSlots(
      [slot(1, 0, 0, 60, 5), slot(2, 0, 0, 22, 5), slot(2, 0, 15, 22, 5)],
      day,
    );
    const load = loadProfile(runs);
    expect(load[0]).toBe(82);
    expect(load[1]).toBe(22);
    expect(load[2]).toBe(0);
    expect(load).toHaveLength(96);
  });
});
