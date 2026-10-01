import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { setSession, decodeJwt } from '../api/auth';
import { api, ApiError } from '../api/client';
import type { DevTokenResponse, Role } from '../api/types';
import { Glyph } from '../components/Glyph';
import './Login.css';

const roles: Role[] = ['admin', 'manager', 'finance', 'auditor'];

export function Login() {
  const navigate = useNavigate();
  const location = useLocation() as { state?: { from?: string } };
  const [tenant, setTenant] = useState('1'); // seed creates tenants 1–8
  const [role, setRole] = useState<Role>('manager');
  const [sub, setSub] = useState('ops@fleet');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<DevTokenResponse>(
        '/auth/dev-token',
        { tenant_id: Number(tenant), role, sub: sub || undefined },
        { auth: false },
      );
      const claims = decodeJwt(r.access_token);
      setSession({
        token: r.access_token,
        tenant_id: Number(claims?.tenant_id ?? tenant),
        role: (claims?.role as Role) ?? role,
        sub: String(claims?.sub ?? sub ?? 'dev-user'),
      });
      navigate(location.state?.from ?? '/console', { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? `${err.status} ${err.message}` : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="login-card panel" onSubmit={submit}>
        <div className="login-brand">
          <Glyph name="orbit" size={40} />
          <h1 className="display">Sign in</h1>
        </div>
        <p className="login-hint">Development issuer · HS256 tokens minted by the API</p>
        <label className="field">
          <span className="label">Tenant ID</span>
          <input
            className="input num"
            type="number"
            min={1}
            value={tenant}
            onChange={(e) => setTenant(e.target.value)}
            required
          />
        </label>
        <label className="field">
          <span className="label">Role</span>
          <select className="select" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="label">Subject (optional)</span>
          <input
            className="input"
            value={sub}
            onChange={(e) => setSub(e.target.value)}
            placeholder="ops@fleet"
          />
        </label>
        {error && (
          <div className="login-error" role="alert">
            {error}
          </div>
        )}
        <button type="submit" className="btn btn-primary btn-lg" disabled={busy}>
          {busy ? 'Signing in…' : 'Get token'}
        </button>
      </form>
    </div>
  );
}
