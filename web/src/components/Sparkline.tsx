interface Props {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  className?: string;
}

/** Tiny SVG sparkline; the current value gets an end-dot in the series colour. */
export function Sparkline({
  data,
  width = 96,
  height = 28,
  color = 'var(--sage)',
  className,
}: Props) {
  if (data.length < 2)
    return <svg width={width} height={height} className={className} aria-hidden="true" />;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const step = width / (data.length - 1);
  const pts = data.map(
    (v, i) => [i * step, height - 3 - ((v - min) / span) * (height - 6)] as const,
  );
  const d = pts
    .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ');
  const last = pts[pts.length - 1]!;
  return (
    <svg
      width={width}
      height={height}
      className={className}
      aria-hidden="true"
      style={{ overflow: 'visible' }}
    >
      <path
        d={d}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
        opacity={0.7}
      />
      <circle
        cx={last[0]}
        cy={last[1]}
        r={3.5}
        fill={color}
        stroke="var(--bg-elev)"
        strokeWidth={2}
      />
    </svg>
  );
}
