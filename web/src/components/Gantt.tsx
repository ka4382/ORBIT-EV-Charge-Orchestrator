import type { CSSProperties } from 'react';
import { DAY_MIN, type MergedRun, type TariffBand } from '../lib/schedule';
import { fmtInr2 } from '../lib/format';
import { Glyph } from './Glyph';
import './Gantt.css';

const HOURS = [0, 3, 6, 9, 12, 15, 18, 21, 24];

export interface GanttRow {
  id: string | number;
  label: string;
  sublabel?: string;
  runs: MergedRun[];
}

interface Props {
  rows: GanttRow[];
  bands?: TariffBand[];
  /** kW per 15-minute bucket, for the capacity/load line. */
  load?: number[];
  capacityKw?: number;
  nowMin?: number;
  onRunClick?: (run: MergedRun, row: GanttRow) => void;
  rowHeight?: number;
}

const pct = (m: number) => `${(Math.max(0, Math.min(DAY_MIN, m)) / DAY_MIN) * 100}%`;

/** Gantt of 15-minute charging runs over a 24 h axis with tariff bands behind. */
export function Gantt({
  rows,
  bands = [],
  load,
  capacityKw,
  nowMin,
  onRunClick,
  rowHeight = 36,
}: Props) {
  const maxLoad = Math.max(capacityKw ?? 0, ...(load ?? [0]), 1);
  const loadPath =
    load && load.length
      ? load
          .map((kw, i) => {
            const x = ((i + 0.5) / load.length) * 100;
            const y = 100 - (kw / maxLoad) * 100;
            return `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
          })
          .join(' ')
      : '';

  return (
    <div className="gantt" style={{ '--row-h': `${rowHeight}px` } as CSSProperties}>
      <div className="gantt-axis">
        <div className="gantt-label" />
        <div className="gantt-track">
          {HOURS.map((h) => (
            <span key={h} className="gantt-tick num" style={{ left: pct(h * 60) }}>
              {String(h).padStart(2, '0')}
            </span>
          ))}
        </div>
      </div>

      {load && (
        <div className="gantt-row gantt-load">
          <div className="gantt-label">
            <span>Depot load</span>
            {capacityKw && <small className="num">cap {capacityKw} kW</small>}
          </div>
          <div className="gantt-track">
            <Bands bands={bands} />
            <svg
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              className="gantt-load-svg"
              aria-hidden="true"
            >
              {capacityKw && (
                <line
                  x1={0}
                  x2={100}
                  y1={100 - (capacityKw / maxLoad) * 100}
                  y2={100 - (capacityKw / maxLoad) * 100}
                  className="gantt-cap"
                  vectorEffect="non-scaling-stroke"
                />
              )}
              <path d={loadPath} className="gantt-load-line" vectorEffect="non-scaling-stroke" />
            </svg>
          </div>
        </div>
      )}

      {rows.map((row) => (
        <div className="gantt-row" key={row.id}>
          <div className="gantt-label">
            <span>{row.label}</span>
            {row.sublabel && <small>{row.sublabel}</small>}
          </div>
          <div className="gantt-track">
            <Bands bands={bands} />
            {nowMin !== undefined && <span className="gantt-now" style={{ left: pct(nowMin) }} />}
            {row.runs.map((r) => (
              <button
                type="button"
                key={`${r.charger_id}-${r.start_ts}`}
                className="gantt-run"
                data-band={r.band}
                style={{
                  left: pct(r.startMin),
                  width: `calc(${pct(r.endMin - r.startMin)} - 2px)`,
                }}
                onClick={() => onRunClick?.(r, row)}
                title={`${r.kw} kW · ${r.kwh.toFixed(1)} kWh · ${fmtInr2(r.cost)} @ ₹${r.price_per_kwh}/kWh`}
              >
                <span className="gantt-run-label num">
                  {r.kw} kW · {fmtInr2(r.cost)}
                </span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function Bands({ bands }: { bands: TariffBand[] }) {
  return (
    <>
      {bands.map((b) => (
        <span
          key={b.startMin}
          className="gantt-band"
          data-band={b.band}
          style={{ left: pct(b.startMin), width: pct(b.endMin - b.startMin) }}
          aria-hidden="true"
        />
      ))}
    </>
  );
}

export function GanttLegend() {
  return (
    <div className="gantt-legend">
      <span className="gl-item">
        <Glyph name="sunbox" size={16} tone="sage" />{' '}
        <i className="gl-swatch" data-band="offpeak" /> Off-peak
      </span>
      <span className="gl-item">
        <i className="gl-swatch" data-band="normal" /> Normal
      </span>
      <span className="gl-item">
        <i className="gl-swatch" data-band="peak" /> Peak
      </span>
      <span className="gl-item">
        <i className="gl-swatch gl-run" /> Planned run
      </span>
    </div>
  );
}
