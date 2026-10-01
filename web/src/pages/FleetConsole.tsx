import { useEffect, useMemo, useRef } from 'react';
import { useFleetSummary } from '../api/hooks';
import type { FleetSummary } from '../api/types';
import { FleetMap } from '../components/FleetMap';
import { KpiTile } from '../components/KpiTile';
import { LiveTimeline } from '../components/LiveTimeline';
import { useUiStore } from '../layout/uiStore';
import { fmtCompact, fmtInrCompact, fmtNum } from '../lib/format';
import { useFleetStore } from '../ws/store';
import './FleetConsole.css';

type Key = keyof FleetSummary;
const TREND_LEN = 24;

/** Keeps the last N values of each summary field for sparklines. */
function useTrends(summary: FleetSummary | null) {
  const ref = useRef<Partial<Record<Key, number[]>>>({});
  useEffect(() => {
    if (!summary) return;
    for (const k of Object.keys(summary) as Key[]) {
      const arr = ref.current[k] ?? [];
      arr.push(summary[k]);
      if (arr.length > TREND_LEN) arr.shift();
      ref.current[k] = arr;
    }
  }, [summary]);
  return ref.current;
}

/** Stream counts come live over the WebSocket; open alerts and money come from REST (Postgres). */
export function mergeSummary(
  rest: FleetSummary | null,
  live: FleetSummary | null,
): FleetSummary | null {
  if (!live) return rest;
  if (!rest) return live;
  return {
    ...rest,
    vehicles: live.vehicles,
    online: live.online,
    charging: live.charging,
    at_risk: live.at_risk,
    avg_soc: live.avg_soc,
    kwh_today: live.kwh_today,
  };
}

export function FleetConsole() {
  const wsSummary = useFleetStore((s) => s.summary);
  const rest = useFleetSummary();
  const summary = mergeSummary(rest.data ?? null, wsSummary);
  const trends = useTrends(summary);
  const openVehicle = useUiStore((s) => s.openVehicle);
  const selectedVin = useUiStore((s) => s.selectedVin);

  const tiles = useMemo(() => {
    if (!summary) return null;
    return [
      {
        label: 'Vehicles online',
        value: fmtNum(summary.online),
        key: 'online' as Key,
        tone: 'sage' as const,
      },
      {
        label: 'Charging now',
        value: fmtNum(summary.charging),
        key: 'charging' as Key,
        tone: 'mist' as const,
      },
      {
        label: 'At risk',
        value: fmtNum(summary.at_risk),
        key: 'at_risk' as Key,
        tone: 'ember' as const,
      },
      {
        label: 'Avg SoC',
        value: `${summary.avg_soc.toFixed(0)}`,
        unit: '%',
        key: 'avg_soc' as Key,
        tone: 'sage' as const,
      },
      {
        label: 'kWh today',
        value: fmtCompact(summary.kwh_today),
        key: 'kwh_today' as Key,
        tone: 'mist' as const,
      },
      {
        label: 'Savings today',
        value: fmtInrCompact(summary.savings_today),
        key: 'savings_today' as Key,
        tone: 'sage' as const,
      },
    ];
  }, [summary]);

  return (
    <div className="fleet">
      <div className="fleet-kpis">
        {tiles
          ? tiles.map((t) => (
              <KpiTile
                key={t.label}
                label={t.label}
                value={t.value}
                unit={t.unit}
                tone={t.tone}
                trend={trends[t.key]}
              />
            ))
          : Array.from({ length: 6 }, (_, i) => <div key={i} className="kpi panel kpi-skeleton" />)}
      </div>
      <div className="fleet-map-wrap">
        <FleetMap onSelect={openVehicle} selectedVin={selectedVin} />
      </div>
      <aside className="fleet-rail panel">
        <LiveTimeline onOpenVehicle={openVehicle} />
      </aside>
    </div>
  );
}
