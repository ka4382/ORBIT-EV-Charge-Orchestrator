import type { Alert } from '../api/types';
import { alertTitles, describeAlert } from '../lib/alerts';
import { fmtAge } from '../lib/format';
import './AlertRow.css';

interface Props {
  alert: Alert;
  onAck?: (a: Alert) => void;
  acking?: boolean;
  onOpenVehicle?: (vin: string) => void;
  now?: number;
}

export function AlertRow({ alert, onAck, acking, onOpenVehicle, now }: Props) {
  const acked = !!alert.acked_by;
  return (
    <div className="alert-row" data-acked={acked}>
      <span className="sev" data-sev={alert.severity} title={alert.severity} />
      <div className="alert-body">
        <div className="alert-head">
          <strong>{alertTitles[alert.type] ?? alert.type}</strong>
          <button
            type="button"
            className="alert-vin mono"
            onClick={() => onOpenVehicle?.(alert.vin)}
          >
            {alert.vin}
          </button>
        </div>
        <div className="alert-desc">{describeAlert(alert)}</div>
      </div>
      <div className="alert-meta">
        <span className="num alert-age" title={new Date(alert.ts).toLocaleString()}>
          {fmtAge(alert.ts, now)}
        </span>
        {acked ? (
          <span className="alert-acked" title={`by ${alert.acked_by}`}>
            Acked
          </span>
        ) : (
          onAck && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => onAck(alert)}
              disabled={acking}
            >
              Ack
            </button>
          )
        )}
      </div>
    </div>
  );
}
