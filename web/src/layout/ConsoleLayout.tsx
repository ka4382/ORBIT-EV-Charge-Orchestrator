import { useEffect, useMemo, useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { clearSession, getSession } from '../api/auth';
import { useFleets } from '../api/hooks';
import { Icon, type IconName } from '../components/Icon';
import { Glyph } from '../components/Glyph';
import { VehicleDrawer } from '../features/vehicles/VehicleDrawer';
import { fmtNum } from '../lib/format';
import { useFleetSocket } from '../ws/useFleetSocket';
import { useFleetStore } from '../ws/store';
import { useUiStore } from './uiStore';
import './ConsoleLayout.css';

const nav: { to: string; icon: IconName; label: string; end?: boolean }[] = [
  { to: '/console', icon: 'home', label: 'Fleet', end: true },
  { to: '/console/vehicles', icon: 'car', label: 'Vehicles' },
  { to: '/console/reports', icon: 'battery', label: 'Reports' },
  { to: '/console/schedule', icon: 'calendar', label: 'Schedule' },
  { to: '/console/alerts', icon: 'pin', label: 'Alerts' },
  { to: '/console/agent', icon: 'history', label: 'Agent' },
];

export function ConsoleLayout() {
  const navigate = useNavigate();
  const session = getSession();
  const fleets = useFleets();
  const fleetId = useUiStore((s) => s.fleetId);
  const setFleet = useUiStore((s) => s.setFleet);
  const openVehicle = useUiStore((s) => s.openVehicle);
  const summary = useFleetStore((s) => s.summary);
  const wsStatus = useFleetStore((s) => s.status);
  const vehicleCount = useFleetStore((s) => Object.keys(s.vehicles).length);
  const [q, setQ] = useState('');

  const sub = useMemo(
    () => (fleetId ? ({ type: 'subscribe', fleet_id: fleetId } as const) : null),
    [fleetId],
  );
  useFleetSocket(sub);

  useEffect(() => {
    document.documentElement.dataset.theme = 'dark';
  }, []);

  const onSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const v = q.trim().toUpperCase();
    if (v) {
      openVehicle(v);
      setQ('');
    }
  };

  const live = summary?.vehicles ?? summary?.online ?? vehicleCount;

  return (
    <div className="console">
      <nav className="sidebar" aria-label="Console">
        <NavLink to="/" className="sidebar-mark" title="ORBIT home">
          <Glyph name="orbit" size={30} alt="ORBIT" />
        </NavLink>
        <ul>
          {nav.map((n) => (
            <li key={n.to}>
              <NavLink
                to={n.to}
                end={n.end}
                className="sidebar-link"
                title={n.label}
                aria-label={n.label}
              >
                <Icon name={n.icon} size={22} />
                <span className="sidebar-label">{n.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <header className="topbar">
        <div className="topbar-left">
          <label className="sr-only" htmlFor="fleet-select">
            Fleet
          </label>
          <select
            id="fleet-select"
            className="select topbar-fleet"
            value={fleetId ?? ''}
            onChange={(e) => setFleet(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">All fleets</option>
            {fleets.data?.items.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
          <div className="live" data-status={wsStatus}>
            <span className="live-dot" aria-hidden="true" />
            <span className="num">LIVE · {fmtNum(live)} vehicles</span>
          </div>
        </div>
        <form className="topbar-search" onSubmit={onSearch} role="search">
          <Icon name="search" size={16} />
          <input
            className="input"
            placeholder="Search VIN"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search VIN"
          />
        </form>
        <div className="topbar-right">
          <div className="user-chip" title={session?.sub ?? ''}>
            <span className="user-avatar">{(session?.sub ?? 'U').slice(0, 1).toUpperCase()}</span>
            <span className="user-meta">
              <strong>{session?.sub ?? 'guest'}</strong>
              <small>
                {session?.role ?? '—'} · tenant {session?.tenant_id ?? '—'}
              </small>
            </span>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              clearSession();
              navigate('/login');
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="console-main">
        <Outlet />
      </main>

      <VehicleDrawer />
    </div>
  );
}
