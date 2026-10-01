import type { CSSProperties } from 'react';
import { socColor } from '../lib/soc';

interface Props {
  soc: number;
  showValue?: boolean;
  width?: number | string;
}

export function SocBar({ soc, showValue = true, width = 120 }: Props) {
  const pct = Math.max(0, Math.min(100, soc));
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, width }}>
      <div
        className="socbar"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        aria-label="State of charge"
        style={{ '--soc-c': socColor(pct) } as CSSProperties}
      >
        <i style={{ width: `${pct}%` }} data-testid="socbar-fill" />
      </div>
      {showValue && (
        <span className="num" style={{ fontSize: 12, minWidth: 34, textAlign: 'right' }}>
          {pct.toFixed(0)}%
        </span>
      )}
    </div>
  );
}
