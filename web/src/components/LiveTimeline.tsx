import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { useAckAlert } from '../api/hooks';
import type { Alert, Page } from '../api/types';
import { fmtAge, fmtTimeSec } from '../lib/format';
import { useFleetStore, type LiveEvent } from '../ws/store';
import { alertTitles, describeAlert } from '../lib/alerts';
import { useToast } from './Toast';
import './LiveTimeline.css';

const statusWord: Record<string, string> = {
  DRIVING: 'driving',
  IDLE: 'idle',
  CHARGING: 'charging',
  OFFLINE: 'offline',
  AT_RISK: 'at risk',
};

function sevOf(e: LiveEvent): string {
  if (e.kind === 'alert') return e.alert.severity;
  if (e.kind === 'state') return e.to === 'AT_RISK' ? 'WARN' : e.to === 'OFFLINE' ? 'INFO' : 'OK';
  return 'INFO';
}

interface Props {
  onOpenVehicle?: (vin: string) => void;
  limit?: number;
}

export function LiveTimeline({ onOpenVehicle, limit = 60 }: Props) {
  const events = useFleetStore((s) => s.events);
  const status = useFleetStore((s) => s.status);
  const ackEvent = useFleetStore((s) => s.ackEvent);
  const applyMessage = useFleetStore((s) => s.applyMessage);
  const ack = useAckAlert();
  // Backfill: recent alerts from REST so the rail is not empty before the first push.
  const recent = useQuery({
    queryKey: ['alerts', 'recent'],
    queryFn: () => api.get<Page<Alert>>('/alerts', { limit: 20 }),
    staleTime: Infinity,
  });
  useEffect(() => {
    if (!recent.data) return;
    for (const a of [...recent.data.items].reverse())
      applyMessage({ type: 'alert', data: a }, Date.parse(a.ts));
  }, [recent.data, applyMessage]);
  const { toast } = useToast();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 10_000);
    return () => clearInterval(t);
  }, []);

  const doAck = (id: string | number) => {
    ack.mutate(id, {
      onSuccess: (r) => ackEvent(r.id, r.acked_by, r.acked_ts),
      onError: (e) => toast(`Ack failed: ${e.message}`, 'error'),
    });
  };

  const shown = events.slice(0, limit);

  return (
    <div className="timeline">
      <div className="timeline-head">
        <span className="label">Live events</span>
        <span className="timeline-status" data-status={status}>
          {status === 'open' ? 'streaming' : status}
        </span>
      </div>
      {shown.length === 0 ? (
        <div className="timeline-empty">Waiting for the first event…</div>
      ) : (
        <ol className="timeline-list">
          {shown.map((e, i) => (
            <li key={e.id} className="tl-item" data-active={i === 0}>
              <span className="tl-dot sev" data-sev={sevOf(e)} />
              <div className="tl-body">
                <div className="tl-meta">
                  <span className="mono tl-time">{fmtTimeSec(e.ts)}</span>
                  <span className="num">{fmtAge(e.ts, now)} ago</span>
                </div>
                {e.kind === 'alert' && (
                  <>
                    <div className="tl-title">
                      {alertTitles[e.alert.type] ?? e.alert.type}
                      <button
                        type="button"
                        className="tl-vin mono"
                        onClick={() => onOpenVehicle?.(e.alert.vin)}
                      >
                        {e.alert.vin.slice(-6)}
                      </button>
                    </div>
                    <div className="tl-desc">{describeAlert(e.alert)}</div>
                    <div className="tl-actions">
                      {e.alert.acked_by ? (
                        <span className="alert-acked">Acked · {e.alert.acked_by}</span>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => doAck(e.alert.id)}
                          disabled={ack.isPending}
                        >
                          Ack
                        </button>
                      )}
                    </div>
                  </>
                )}
                {e.kind === 'state' && (
                  <>
                    <div className="tl-title">
                      {e.from ? `${statusWord[e.from] ?? e.from} → ` : ''}
                      {statusWord[e.to] ?? e.to}
                      <button
                        type="button"
                        className="tl-vin mono"
                        onClick={() => onOpenVehicle?.(e.vin)}
                      >
                        {e.vin.slice(-6)}
                      </button>
                    </div>
                    <div className="tl-desc num">SoC {e.soc.toFixed(0)}%</div>
                  </>
                )}
                {e.kind === 'plan' && (
                  <>
                    <div className="tl-title">
                      Plan {e.status.toLowerCase()}
                      <button
                        type="button"
                        className="tl-vin mono"
                        onClick={() => onOpenVehicle?.(e.vin)}
                      >
                        {e.vin.slice(-6)}
                      </button>
                    </div>
                    <div className="tl-desc">plan #{String(e.plan_id)}</div>
                  </>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
