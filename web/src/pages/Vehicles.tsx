import { useCallback, useState } from 'react';
import { api } from '../api/client';
import { useCursorPagination } from '../api/useCursorPagination';
import type { Page, Vehicle, VehicleStatus } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { SocBar } from '../components/SocBar';
import { StatusChip } from '../components/StatusChip';
import { useUiStore } from '../layout/uiStore';
import { fmtAge } from '../lib/format';
import { useFleetStore } from '../ws/store';
import './Vehicles.css';

const statuses: VehicleStatus[] = ['DRIVING', 'IDLE', 'CHARGING', 'AT_RISK', 'OFFLINE'];

export function Vehicles() {
  const fleetId = useUiStore((s) => s.fleetId);
  const [status, setStatus] = useState<string>('');
  const openVehicle = useUiStore((s) => s.openVehicle);
  const selectedVin = useUiStore((s) => s.selectedVin);
  const live = useFleetStore((s) => s.vehicles);

  const fetcher = useCallback(
    (cursor: string | null) =>
      api.get<Page<Vehicle>>('/vehicles', {
        fleet_id: fleetId ?? undefined,
        status: status || undefined,
        limit: 50,
        cursor,
      }),
    [fleetId, status],
  );
  const page = useCursorPagination(fetcher, `${fleetId ?? 'all'}:${status}`);

  return (
    <div className="page">
      <div className="page-title">
        <h1 className="display">Vehicles</h1>
        <div className="filters">
          <label className="field">
            <span className="sr-only">Status</span>
            <select
              className="select"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              aria-label="Status filter"
            >
              <option value="">All statuses</option>
              {statuses.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="panel vehicles-panel">
        <table className="table">
          <thead>
            <tr>
              <th>VIN</th>
              <th>Plate</th>
              <th>Model</th>
              <th>Status</th>
              <th>SoC</th>
              <th>SoH</th>
              <th>Last seen</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map((v) => {
              const st = live[v.vin] ?? v.state;
              return (
                <tr
                  key={v.vin}
                  onClick={() => openVehicle(v.vin)}
                  aria-selected={selectedVin === v.vin}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      openVehicle(v.vin);
                    }
                  }}
                >
                  <td className="mono">{v.vin}</td>
                  <td>{v.plate}</td>
                  <td>{v.model}</td>
                  <td>
                    <StatusChip status={st?.status} />
                  </td>
                  <td>{st ? <SocBar soc={st.soc_pct} /> : <span className="vd-muted">—</span>}</td>
                  <td className="num">{st ? `${st.soh_pct.toFixed(1)}%` : '—'}</td>
                  <td className="num" title={st?.ts}>
                    {st ? `${fmtAge(st.ts)} ago` : 'never'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {page.items.length === 0 && !page.loading && (
          <EmptyState text="No vehicles match this filter." />
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
          {page.loading && page.items.length === 0 && <span className="vd-muted">Loading…</span>}
        </div>
      </div>
    </div>
  );
}
