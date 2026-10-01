import { useEffect, useMemo, useState } from 'react';
import {
  useCreatePlan,
  useNearestChargers,
  usePlan,
  useRangeForecast,
  useVehicle,
  useVehiclePlans,
} from '../../api/hooks';
import { Drawer } from '../../components/Drawer';
import { Gantt, GanttLegend } from '../../components/Gantt';
import { SocBar } from '../../components/SocBar';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../components/Toast';
import { useUiStore } from '../../layout/uiStore';
import { fmtAge, fmtDateTime, fmtInr2, fmtNum, toLocalInput } from '../../lib/format';
import { mergeSlots, minuteOfDay, startOfToday } from '../../lib/schedule';
import { useFleetStore } from '../../ws/store';
import './VehicleDrawer.css';

export function VehicleDrawer() {
  const vin = useUiStore((s) => s.selectedVin);
  const close = useUiStore((s) => s.closeVehicle);
  return (
    <Drawer open={!!vin} onClose={close} title={<DrawerTitle vin={vin} />}>
      {vin && <VehicleDetail vin={vin} />}
    </Drawer>
  );
}

function DrawerTitle({ vin }: { vin: string | null }) {
  const v = useVehicle(vin);
  return (
    <div>
      <div className="mono" style={{ color: 'var(--fg-muted)', fontSize: 12 }}>
        {vin ?? ''}
      </div>
      <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--fg-strong)' }}>
        {v.data ? `${v.data.plate} · ${v.data.model}` : '…'}
      </div>
    </div>
  );
}

export function VehicleDetail({ vin }: { vin: string }) {
  const vehicle = useVehicle(vin);
  const liveState = useFleetStore((s) => s.vehicles[vin]);
  const state = liveState ?? vehicle.data?.state ?? null;
  const nearest = useNearestChargers(vin);
  const range = useRangeForecast(vin);
  const plans = useVehiclePlans(vin);
  const latestId = plans.data?.items[0]?.id ?? null;
  const plan = usePlan(latestId, true);
  const create = useCreatePlan();
  const { toast } = useToast();

  const [targetSoc, setTargetSoc] = useState(90);
  const [depart, setDepart] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(7, 0, 0, 0);
    return toLocalInput(d);
  });

  useEffect(() => {
    if (create.isSuccess) {
      toast(`Plan #${String(create.data.plan_id)} requested`);
      create.reset();
    }
  }, [create, toast]);

  const runs = useMemo(
    () => (plan.data ? mergeSlots(plan.data.slots, startOfToday()) : []),
    [plan.data],
  );
  const ganttRows = useMemo(() => {
    const byCharger = new Map<number, typeof runs>();
    for (const r of runs) byCharger.set(r.charger_id, [...(byCharger.get(r.charger_id) ?? []), r]);
    return Array.from(byCharger.entries()).map(([id, rs]) => ({
      id,
      label: `Charger ${id}`,
      runs: rs,
    }));
  }, [runs]);

  if (vehicle.isError) {
    return <div className="empty">Vehicle not found: {vehicle.error.message}</div>;
  }

  return (
    <>
      <section className="drawer-section">
        <div className="vd-state">
          <StatusChip status={state?.status} />
          <span className="num vd-seen">{state ? `seen ${fmtAge(state.ts)} ago` : 'no state'}</span>
        </div>
        <div className="vd-grid">
          <Stat label="SoC" value={state ? `${state.soc_pct.toFixed(0)}%` : '—'} />
          <Stat label="SoH" value={state ? `${state.soh_pct.toFixed(1)}%` : '—'} />
          <Stat label="Speed" value={state ? `${state.speed_kmh.toFixed(0)} km/h` : '—'} />
          <Stat label="Odometer" value={state ? `${fmtNum(Math.round(state.odo_km))} km` : '—'} />
          <Stat
            label="Charging"
            value={
              state && state.charging_kw > 0
                ? `${state.charging_kw} kW ${state.charging_state}`
                : 'no'
            }
          />
          <Stat
            label="Range forecast"
            value={range.data ? `${fmtNum(Math.round(range.data.range_km))} km` : '…'}
            hint={
              range.data
                ? `${range.data.method} · ${(range.data.confidence * 100).toFixed(0)}%`
                : undefined
            }
          />
        </div>
        {state && <SocBar soc={state.soc_pct} width="100%" />}
      </section>

      <section className="drawer-section">
        <h3>Nearest chargers</h3>
        {nearest.data?.items.length ? (
          <ul className="vd-list">
            {nearest.data.items.map((c) => (
              <li key={c.charger_id} className="vd-charger" data-reachable={c.reachable}>
                <span
                  className="sev"
                  data-sev={c.reachable ? 'OK' : 'WARN'}
                  title={c.reachable ? 'Reachable' : 'Out of range'}
                />
                <span className="vd-charger-name">{c.name}</span>
                <span className="num vd-charger-meta">
                  {c.distance_km.toFixed(1)} km · {c.max_kw} kW ·{' '}
                  {c.price_per_kwh != null ? `₹${c.price_per_kwh}` : 'depot tariff'}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="vd-muted">
            {nearest.isLoading ? 'Finding chargers…' : 'No chargers nearby'}
          </div>
        )}
      </section>

      <section className="drawer-section">
        <h3>Diagnostic trouble codes</h3>
        {state?.dtc?.length ? (
          <div className="vd-dtcs">
            {state.dtc.map((d) => (
              <span key={d} className="mono vd-dtc">
                {d}
              </span>
            ))}
          </div>
        ) : (
          <div className="vd-muted">None active</div>
        )}
      </section>

      <section className="drawer-section">
        <h3>Plan charge</h3>
        <form
          className="vd-form"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(
              { vin, target_soc: targetSoc, depart_ts: new Date(depart).toISOString() },
              { onError: (err) => toast(`Plan failed: ${err.message}`, 'error') },
            );
          }}
        >
          <label className="field">
            <span className="label">Target SoC</span>
            <div className="vd-range">
              <input
                type="range"
                min={20}
                max={100}
                step={5}
                value={targetSoc}
                onChange={(e) => setTargetSoc(Number(e.target.value))}
                aria-label="Target SoC"
              />
              <span className="num">{targetSoc}%</span>
            </div>
          </label>
          <label className="field">
            <span className="label">Depart</span>
            <input
              type="datetime-local"
              className="input"
              value={depart}
              onChange={(e) => setDepart(e.target.value)}
              required
            />
          </label>
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            {create.isPending ? 'Requesting…' : 'Plan charge'}
          </button>
        </form>
      </section>

      <section className="drawer-section">
        <h3>Latest plan</h3>
        {plan.data ? (
          <div className="vd-plan">
            <div className="vd-plan-head">
              <span
                className="chip"
                data-status={plan.data.status === 'PENDING' ? 'OFFLINE' : 'DRIVING'}
              >
                {plan.data.status}
              </span>
              <span className="num vd-muted">
                depart {fmtDateTime(plan.data.depart_ts)} · target {plan.data.target_soc}%
              </span>
            </div>
            {runs.length > 0 ? (
              <>
                <Gantt rows={ganttRows} nowMin={minuteOfDay(new Date())} rowHeight={32} />
                <GanttLegend />
                <table className="table vd-runs">
                  <thead>
                    <tr>
                      <th>Run</th>
                      <th>kW</th>
                      <th>kWh</th>
                      <th>₹/kWh</th>
                      <th>Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((r) => (
                      <tr key={`${r.charger_id}-${r.start_ts}`} style={{ cursor: 'default' }}>
                        <td className="num">
                          {fmtDateTime(r.start_ts).slice(-5)}–{fmtDateTime(r.end_ts).slice(-5)}
                        </td>
                        <td className="num">{r.kw}</td>
                        <td className="num">{r.kwh.toFixed(1)}</td>
                        <td className="num">{r.price_per_kwh}</td>
                        <td className="num">{fmtInr2(r.cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="vd-plan-total">
                  <span>Total {fmtInr2(plan.data.total_cost)}</span>
                  <span className="vd-muted">baseline {fmtInr2(plan.data.baseline_cost)}</span>
                  <span style={{ color: 'var(--sage)' }}>saves {fmtInr2(plan.data.savings)}</span>
                </div>
              </>
            ) : (
              <div className="vd-muted">Planner is working on it…</div>
            )}
          </div>
        ) : (
          <div className="vd-muted">{plans.isLoading ? 'Loading plans…' : 'No plan yet'}</div>
        )}
      </section>
    </>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="vd-stat">
      <span className="label">{label}</span>
      <span className="num vd-stat-value">{value}</span>
      {hint && <small className="vd-muted">{hint}</small>}
    </div>
  );
}
