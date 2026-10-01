import { useCallback, useState } from 'react';
import { api } from '../api/client';
import { useAckAlert } from '../api/hooks';
import { useCursorPagination } from '../api/useCursorPagination';
import type { Alert, Page } from '../api/types';
import { AlertRow } from '../components/AlertRow';
import { EmptyState } from '../components/EmptyState';
import { useToast } from '../components/Toast';
import { useUiStore } from '../layout/uiStore';
import { useFleetStore } from '../ws/store';

const severities = ['CRITICAL', 'WARN', 'INFO'];
const types = [
  'STRAND_RISK',
  'LOW_SOC',
  'SOC_DROP',
  'SOH_DROP',
  'BATTERY_DTC',
  'CHARGE_INTERRUPTED',
  'OFF_PLAN',
];

export function Alerts() {
  const [severity, setSeverity] = useState('');
  const [type, setType] = useState('');
  const [acked, setAcked] = useState<'' | 'true' | 'false'>('false');
  const openVehicle = useUiStore((s) => s.openVehicle);
  const ackEvent = useFleetStore((s) => s.ackEvent);
  const ack = useAckAlert();
  const { toast } = useToast();

  const fetcher = useCallback(
    (cursor: string | null) =>
      api.get<Page<Alert>>('/alerts', {
        severity: severity || undefined,
        type: type || undefined,
        acked: acked || undefined,
        limit: 20,
        cursor,
      }),
    [severity, type, acked],
  );
  const page = useCursorPagination(fetcher, `${severity}|${type}|${acked}`);

  const doAck = (a: Alert) =>
    ack.mutate(a.id, {
      onSuccess: (r) => {
        page.update(
          (x) => String(x.id) === String(r.id),
          (x) => ({ ...x, acked_by: r.acked_by, acked_ts: r.acked_ts }),
        );
        ackEvent(r.id, r.acked_by, r.acked_ts);
        toast(`Acknowledged ${a.type}`);
      },
      onError: (e) => toast(`Ack failed: ${e.message}`, 'error'),
    });

  return (
    <div className="page">
      <div className="page-title">
        <h1 className="display">Alerts</h1>
        <div className="filters">
          <select
            className="select"
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
            aria-label="Severity"
          >
            <option value="">All severities</option>
            {severities.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select
            className="select"
            value={type}
            onChange={(e) => setType(e.target.value)}
            aria-label="Type"
          >
            <option value="">All types</option>
            {types.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <select
            className="select"
            value={acked}
            onChange={(e) => setAcked(e.target.value as '' | 'true' | 'false')}
            aria-label="Acknowledged"
          >
            <option value="">Open and acked</option>
            <option value="false">Open</option>
            <option value="true">Acked</option>
          </select>
        </div>
      </div>

      <div className="panel">
        {page.items.map((a) => (
          <AlertRow
            key={String(a.id)}
            alert={a}
            onAck={doAck}
            acking={ack.isPending}
            onOpenVehicle={openVehicle}
          />
        ))}
        {page.items.length === 0 && !page.loading && (
          <EmptyState text="No alerts match this filter." />
        )}
        {page.error && (
          <div className="vehicles-error" role="alert">
            {page.error.message}
          </div>
        )}
        <div className="vehicles-foot">
          <span className="num vd-muted">{page.items.length} loaded</span>
          {page.hasMore && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => void page.loadMore()}
              disabled={page.loading}
            >
              {page.loading ? 'Loading…' : 'Load more'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
