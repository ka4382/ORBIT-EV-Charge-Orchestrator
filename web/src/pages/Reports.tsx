import { useMemo } from 'react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useBatteryHealth, useCostReport } from '../api/hooks';
import { EmptyState } from '../components/EmptyState';
import { KpiTile } from '../components/KpiTile';
import { Sparkline } from '../components/Sparkline';
import { useUiStore } from '../layout/uiStore';
import { fmtInr, fmtInrCompact, fmtNum, isoDate } from '../lib/format';
import { ApiError } from '../api/client';
import './Reports.css';

export function Reports() {
  const to = useMemo(() => isoDate(new Date()), []);
  const from = useMemo(() => isoDate(new Date(Date.now() - 13 * 86_400_000)), []);
  const cost = useCostReport(from, to);
  const battery = useBatteryHealth();
  const openVehicle = useUiStore((s) => s.openVehicle);

  const days = useMemo(() => cost.data?.days ?? [], [cost.data]);
  const totals = useMemo(
    () =>
      days.reduce(
        (a, d) => ({
          cost: a.cost + d.cost,
          baseline: a.baseline + d.baseline_cost,
          savings: a.savings + d.savings,
          kwh: a.kwh + d.kwh,
        }),
        { cost: 0, baseline: 0, savings: 0, kwh: 0 },
      ),
    [days],
  );
  const chartData = days.map((d) => ({
    ...d,
    day: new Date(d.date).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }),
  }));

  const forbidden = cost.error instanceof ApiError && cost.error.status === 403;

  return (
    <div className="page">
      <div className="page-title">
        <div>
          <div className="label">
            {from} → {to}
          </div>
          <h1 className="display">Reports</h1>
        </div>
      </div>

      <section className="reports-section">
        <div className="reports-kpis">
          <KpiTile label="Energy" value={fmtNum(Math.round(totals.kwh))} unit="kWh" tone="mist" />
          <KpiTile label="Cost" value={fmtInrCompact(totals.cost)} tone="mist" />
          <KpiTile label="Baseline" value={fmtInrCompact(totals.baseline)} tone="mist" />
          <KpiTile
            label="Savings"
            value={fmtInrCompact(totals.savings)}
            tone="sage"
            delta={
              totals.baseline
                ? `${((totals.savings / totals.baseline) * 100).toFixed(1)}% below flat tariff`
                : undefined
            }
          />
        </div>
        <div className="panel reports-chart">
          <div className="panel-head">
            <h2>Cost per day vs flat-tariff baseline</h2>
            <span className="vd-muted">INR</span>
          </div>
          {forbidden ? (
            <EmptyState text="Cost report needs the finance, manager or admin role." />
          ) : days.length === 0 ? (
            <EmptyState text={cost.isLoading ? 'Loading…' : 'No data in range.'} />
          ) : (
            <div className="reports-chart-body">
              <ResponsiveContainer width="100%" height={280}>
                <ComposedChart
                  data={chartData}
                  margin={{ top: 12, right: 16, bottom: 0, left: 8 }}
                  barGap={2}
                >
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="day" tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
                  <YAxis
                    tickFormatter={(v: number) => fmtInrCompact(v)}
                    tickLine={false}
                    axisLine={false}
                    width={72}
                  />
                  <Tooltip
                    cursor={{ fill: 'rgba(229,231,235,0.05)' }}
                    contentStyle={{
                      background: 'var(--bg-elev)',
                      border: '1px solid var(--line-strong)',
                      borderRadius: 4,
                      fontSize: 12,
                    }}
                    labelStyle={{ color: 'var(--fg-muted)' }}
                    formatter={(v: number, name: string) => [fmtInr(v), name]}
                  />
                  <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                  <Bar
                    dataKey="baseline_cost"
                    name="Baseline"
                    fill="var(--moss)"
                    radius={[4, 4, 0, 0]}
                    maxBarSize={24}
                  />
                  <Bar
                    dataKey="cost"
                    name="Actual cost"
                    fill="var(--sage)"
                    radius={[4, 4, 0, 0]}
                    maxBarSize={24}
                  />
                  <Line
                    type="monotone"
                    dataKey="savings"
                    name="Savings"
                    stroke="var(--ember)"
                    strokeWidth={2}
                    dot={{ r: 4, strokeWidth: 2, stroke: 'var(--bg-elev)', fill: 'var(--ember)' }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </section>

      <section className="reports-section">
        <div className="panel">
          <div className="panel-head">
            <h2>Battery health</h2>
            <span className="vd-muted">
              State of health, 30-day trend, similar degradation profiles (pgvector)
            </span>
          </div>
          {battery.data?.items.length ? (
            <div style={{ overflowX: 'auto' }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>VIN</th>
                    <th>SoH</th>
                    <th>Cycles</th>
                    <th>30-day trend</th>
                    <th>Similar vehicles</th>
                  </tr>
                </thead>
                <tbody>
                  {battery.data.items.map((r) => (
                    <tr key={r.vin} onClick={() => openVehicle(r.vin)}>
                      <td className="mono">{r.vin}</td>
                      <td className="num">
                        <span style={{ color: r.soh_pct < 80 ? 'var(--ember)' : undefined }}>
                          {r.soh_pct.toFixed(1)}%
                        </span>
                      </td>
                      <td className="num">{fmtNum(r.cycles)}</td>
                      <td>
                        <span className="trend">
                          <Sparkline
                            data={trendSeries(r.soh_pct, r.trend_30d)}
                            width={72}
                            height={20}
                            color={r.trend_30d < -1.5 ? 'var(--ember)' : 'var(--sage)'}
                          />
                          <span className="num">
                            {r.trend_30d > 0 ? '+' : ''}
                            {r.trend_30d.toFixed(1)} pts
                          </span>
                        </span>
                      </td>
                      <td>
                        {r.similar.map((s) => (
                          <button
                            type="button"
                            key={s}
                            className="btn btn-ghost btn-sm mono"
                            onClick={(e) => {
                              e.stopPropagation();
                              openVehicle(s);
                            }}
                          >
                            {s.slice(-6)}
                          </button>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState text={battery.isLoading ? 'Loading…' : 'No battery health rows yet.'} />
          )}
        </div>
      </section>
    </div>
  );
}

/** Reconstruct a plausible 30-day series from the current value and the delta. */
function trendSeries(current: number, delta30d: number, points = 12): number[] {
  const out: number[] = [];
  for (let i = 0; i < points; i++) {
    const t = i / (points - 1);
    out.push(current - delta30d * (1 - t));
  }
  return out;
}
