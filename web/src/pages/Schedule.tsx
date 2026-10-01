import { useMemo, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { api } from '../api/client';
import { useAllPlans, useChargers, useTariff } from '../api/hooks';
import type { Plan } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { Gantt, GanttLegend, type GanttRow } from '../components/Gantt';
import { KpiTile } from '../components/KpiTile';
import { useUiStore } from '../layout/uiStore';
import { fmtInr, fmtNum } from '../lib/format';
import {
  loadProfile,
  mergeSlots,
  minuteOfDay,
  startOfToday,
  tariffBands,
  type MergedRun,
} from '../lib/schedule';
import './Schedule.css';

const DEPOT_CAPACITY_KW = 200;

export function Schedule() {
  const plans = useAllPlans();
  const chargers = useChargers();
  const tariff = useTariff();
  const openVehicle = useUiStore((s) => s.openVehicle);
  const [selected, setSelected] = useState<{ run: MergedRun; vin: string } | null>(null);

  const todayStart = useMemo(() => startOfToday(), []);
  const todayEnd = useMemo(() => new Date(todayStart.getTime() + 86_400_000), [todayStart]);

  const todaysPlans = useMemo(
    () =>
      (plans.data ?? []).filter((p) => {
        return p.status !== 'FAILED' && p.status !== 'PENDING';
      }),
    [plans.data],
  );

  const details = useQueries({
    queries: todaysPlans.map((p) => ({
      queryKey: ['plan', String(p.id)],
      queryFn: () => api.get<Plan>(`/plans/${p.id}`),
      staleTime: 30_000,
    })),
  });

  const full = details.map((d) => d.data).filter((d): d is Plan => !!d);

  const { rows, runs, totals } = useMemo(() => {
    const byCharger = new Map<number, { runs: MergedRun[]; vins: Map<string, string> }>();
    const allRuns: (MergedRun & { vin: string })[] = [];
    let kwh = 0;
    let cost = 0;
    let baseline = 0;
    for (const p of full) {
      const slots = p.slots.filter((s) => {
        const t = Date.parse(s.start_ts);
        return t >= todayStart.getTime() && t < todayEnd.getTime();
      });
      if (slots.length === 0) continue;
      const rs = mergeSlots(slots, todayStart);
      for (const r of rs) {
        const entry = byCharger.get(r.charger_id) ?? {
          runs: [] as MergedRun[],
          vins: new Map<string, string>(),
        };
        entry.runs.push(r);
        entry.vins.set(`${r.charger_id}-${r.start_ts}`, p.vin);
        byCharger.set(r.charger_id, entry);
        allRuns.push({ ...r, vin: p.vin });
        kwh += r.kwh;
        cost += r.cost;
      }
      const share = slots.length / Math.max(1, p.slots.length);
      baseline += p.baseline_cost * share;
    }
    const chargerName = (id: number) =>
      chargers.data?.items.find((c) => c.id === id)?.name ?? `Charger ${id}`;
    const chargerKw = (id: number) => chargers.data?.items.find((c) => c.id === id)?.max_kw;
    const ids = new Set<number>([
      ...Array.from(byCharger.keys()),
      ...(chargers.data?.items.filter((c) => !c.public).map((c) => c.id) ?? []),
    ]);
    const rows: (GanttRow & { vins: Map<string, string> })[] = Array.from(ids)
      .sort((a, b) => a - b)
      .map((id) => ({
        id,
        label: chargerName(id),
        sublabel: chargerKw(id) ? `${chargerKw(id)} kW` : undefined,
        runs: byCharger.get(id)?.runs ?? [],
        vins: byCharger.get(id)?.vins ?? new Map(),
      }));
    return {
      rows,
      runs: allRuns,
      totals: { kwh, cost, baseline, savings: baseline - cost, sessions: full.length },
    };
  }, [full, chargers.data, todayStart, todayEnd]);

  const bands = useMemo(
    () => tariffBands(tariff.data, todayStart.getDay()),
    [tariff.data, todayStart],
  );
  const load = useMemo(() => loadProfile(runs), [runs]);
  const peakKw = Math.max(0, ...load);

  const loading = plans.isLoading || details.some((d) => d.isLoading);

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <div className="label">
            Depot ·{' '}
            {todayStart.toLocaleDateString('en-GB', {
              weekday: 'long',
              day: 'numeric',
              month: 'short',
            })}
          </div>
          <h1 className="display">Schedule</h1>
        </div>
        <GanttLegend />
      </div>

      <div className="schedule-kpis">
        <KpiTile label="Planned kWh" value={fmtNum(Math.round(totals.kwh))} tone="sage" />
        <KpiTile label="Planned cost" value={fmtInr(totals.cost)} tone="mist" />
        <KpiTile label="Baseline cost" value={fmtInr(totals.baseline)} tone="mist" />
        <KpiTile
          label="Savings"
          value={fmtInr(totals.savings)}
          tone="ember"
          delta={
            totals.baseline
              ? `${((totals.savings / totals.baseline) * 100).toFixed(0)}% below baseline`
              : undefined
          }
        />
        <KpiTile
          label="Peak load"
          value={`${fmtNum(peakKw)}`}
          unit={`/ ${DEPOT_CAPACITY_KW} kW`}
          tone={peakKw > DEPOT_CAPACITY_KW ? 'ember' : 'sage'}
        />
      </div>

      <div className="panel schedule-panel">
        {rows.length > 0 ? (
          <Gantt
            rows={rows}
            bands={bands}
            load={load}
            capacityKw={DEPOT_CAPACITY_KW}
            nowMin={minuteOfDay(new Date())}
            onRunClick={(run, row) => {
              const vin =
                (row as GanttRow & { vins: Map<string, string> }).vins.get(
                  `${run.charger_id}-${run.start_ts}`,
                ) ?? '';
              setSelected({ run, vin });
            }}
            rowHeight={40}
          />
        ) : (
          <EmptyState text={loading ? 'Loading today’s plans…' : 'Nothing planned for today.'} />
        )}
      </div>

      {selected && (
        <div className="panel schedule-detail">
          <div>
            <span className="label">Run</span>
            <div className="num">
              {new Date(selected.run.start_ts).toLocaleTimeString('en-GB', {
                hour: '2-digit',
                minute: '2-digit',
              })}
              –
              {new Date(selected.run.end_ts).toLocaleTimeString('en-GB', {
                hour: '2-digit',
                minute: '2-digit',
              })}
              · {selected.run.kw} kW · {selected.run.kwh.toFixed(1)} kWh · ₹
              {selected.run.price_per_kwh}/kWh · {fmtInr(selected.run.cost)} · {selected.run.band}
            </div>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm mono"
            onClick={() => openVehicle(selected.vin)}
          >
            {selected.vin}
          </button>
        </div>
      )}
    </div>
  );
}
