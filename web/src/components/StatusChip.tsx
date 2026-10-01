const labels: Record<string, string> = {
  DRIVING: 'Driving',
  IDLE: 'Idle',
  CHARGING: 'Charging',
  OFFLINE: 'Offline',
  AT_RISK: 'At risk',
};

export function StatusChip({ status }: { status: string | null | undefined }) {
  const s = status ?? 'OFFLINE';
  return (
    <span className="chip" data-status={s}>
      {labels[s] ?? s}
    </span>
  );
}
