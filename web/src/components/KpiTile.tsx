import { Sparkline } from './Sparkline';
import './KpiTile.css';

interface Props {
  label: string;
  value: string;
  unit?: string;
  trend?: number[];
  tone?: 'sage' | 'ember' | 'mist';
  delta?: string;
}

const toneColor = { sage: 'var(--sage)', ember: 'var(--ember)', mist: 'var(--mist)' };

export function KpiTile({ label, value, unit, trend, tone = 'sage', delta }: Props) {
  return (
    <div className="kpi panel" data-tone={tone}>
      <div className="kpi-label label">{label}</div>
      <div className="kpi-value display">
        {value}
        {unit && <span className="kpi-unit">{unit}</span>}
      </div>
      {(delta || (trend && trend.length > 1)) && (
        <div className="kpi-foot">
          <span className="kpi-delta num">{delta ?? ''}</span>
          {trend && trend.length > 1 && (
            <Sparkline
              data={trend}
              width={80}
              height={20}
              color={toneColor[tone]}
              className="kpi-spark"
            />
          )}
        </div>
      )}
    </div>
  );
}
