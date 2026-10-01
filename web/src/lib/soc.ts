export const SOC_LOW = 20;
export const SOC_CRITICAL = 15;

export type SocTone = 'ok' | 'warn' | 'critical';

/** sage above 20, ember at/below 20, danger below 15 (matches STRAND_RISK threshold). */
export function socTone(soc: number): SocTone {
  if (soc < SOC_CRITICAL) return 'critical';
  if (soc < SOC_LOW) return 'warn';
  return 'ok';
}

export function socColor(soc: number): string {
  switch (socTone(soc)) {
    case 'critical':
      return 'var(--danger)';
    case 'warn':
      return 'var(--ember)';
    default:
      return 'var(--sage)';
  }
}

export function statusColor(status: string): string {
  switch (status) {
    case 'AT_RISK':
      return 'var(--ember)';
    case 'CHARGING':
      return 'var(--paper)';
    case 'OFFLINE':
      return 'var(--moss)';
    default:
      return 'var(--sage)';
  }
}
