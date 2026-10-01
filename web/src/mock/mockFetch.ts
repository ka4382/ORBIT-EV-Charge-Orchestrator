import type { Page } from '../api/types';
import {
  chargers,
  makeBattery,
  makeCostDays,
  nearestFor,
  summaryFrom,
  tariff,
  fleets,
  TENANT_ID,
} from './data';
import { world } from './world';

const PREFIX = '/api/v1';

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  });
}

function problem(status: number, title: string, detail?: string) {
  return new Response(
    JSON.stringify({ type: 'about:blank', title, status, detail, trace_id: 'mock-trace' }),
    { status, headers: { 'content-type': 'application/problem+json' } },
  );
}

function paginate<T>(items: T[], params: URLSearchParams, defaultLimit = 50, cap = 200): Page<T> {
  const limit = Math.max(1, Math.min(cap, Number(params.get('limit') ?? defaultLimit)));
  const offset = Number(params.get('cursor') ?? 0) || 0;
  const slice = items.slice(offset, offset + limit);
  const next = offset + limit < items.length ? String(offset + limit) : null;
  return { items: slice, next_cursor: next };
}

async function bodyOf(init?: RequestInit): Promise<Record<string, unknown>> {
  if (!init?.body) return {};
  try {
    return JSON.parse(String(init.body)) as Record<string, unknown>;
  } catch {
    return {};
  }
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Route a request to the in-memory world. Returns null if not an API call. */
export async function mockRoute(input: string, init?: RequestInit): Promise<Response | null> {
  const url = new URL(input, 'http://mock.local');
  if (!url.pathname.startsWith(PREFIX)) return null;
  const path = url.pathname.slice(PREFIX.length);
  const q = url.searchParams;
  const method = (init?.method ?? 'GET').toUpperCase();
  await delay(60 + Math.random() * 80);

  const auth = (init?.headers as Record<string, string> | undefined)?.['Authorization'];
  if (path !== '/auth/dev-token' && !auth) {
    return problem(401, 'Unauthorized', 'Missing bearer token');
  }

  if (path === '/auth/dev-token' && method === 'POST') {
    const b = await bodyOf(init);
    const payload = btoa(
      JSON.stringify({
        sub: b.sub ?? 'dev-user',
        tenant_id: b.tenant_id ?? TENANT_ID,
        role: b.role ?? 'manager',
        iss: 'orbit-dev',
        exp: Math.floor(Date.now() / 1000) + 3600,
      }),
    )
      .replace(/=+$/, '')
      .replace(/\+/g, '-')
      .replace(/\//g, '_');
    return json({ access_token: `eyJhbGciOiJIUzI1NiJ9.${payload}.mock`, expires_in: 3600 });
  }
  if (path === '/me') return json({ sub: 'dev-user', tenant_id: TENANT_ID, role: 'manager' });
  if (path === '/fleets') return json({ items: fleets });
  if (path === '/fleet/summary') return json(summaryFrom(world.states));

  if (path === '/vehicles') {
    let items = world.vehicles.map((v) => v.vehicle);
    const status = q.get('status');
    if (status) items = items.filter((v) => v.state?.status === status);
    const fleet = q.get('fleet_id');
    if (fleet) items = items.filter((v) => String(v.fleet_id) === fleet);
    // Small pages in mock mode so keyset "Load more" is exercised.
    return json(paginate(items, q, 6, 6));
  }

  let m = path.match(/^\/vehicles\/([^/]+)(\/[a-z-]+)?$/);
  if (m) {
    const vin = decodeURIComponent(m[1]!);
    const v = world.vehicles.find((x) => x.vehicle.vin === vin);
    if (!v) return problem(404, 'Not found', `Vehicle ${vin} not found`);
    switch (m[2]) {
      case undefined:
        return json(v.vehicle);
      case '/state':
        return json(v.state);
      case '/nearest-chargers':
        return json({ items: nearestFor(v.state) });
      case '/range-forecast':
        return json({
          range_km: Math.round(v.state.soc_pct * 2.6),
          method: 'gbm_v2',
          confidence: 0.82,
        });
      case '/telemetry':
        return json({ items: [], next_cursor: null });
    }
  }

  if (path === '/plans' && method === 'POST') {
    const b = await bodyOf(init);
    const p = world.createPlan(String(b.vin), Number(b.target_soc), String(b.depart_ts));
    return json({ plan_id: p.id, status: 'PENDING' }, 202);
  }
  if (path === '/plans') {
    let items = world.plans;
    const vin = q.get('vin');
    if (vin) items = items.filter((p) => p.vin === vin);
    const status = q.get('status');
    if (status) items = items.filter((p) => p.status === status);
    return json(
      paginate(
        items.map(({ slots: _s, ...rest }) => rest),
        q,
      ),
    );
  }
  m = path.match(/^\/plans\/([^/]+)$/);
  if (m) {
    const p = world.plans.find((x) => String(x.id) === m![1]);
    return p ? json(p) : problem(404, 'Not found');
  }

  if (path === '/alerts') {
    let items = [...world.alerts].sort((a, b) => b.ts.localeCompare(a.ts));
    const sev = q.get('severity');
    if (sev) items = items.filter((a) => a.severity === sev);
    const acked = q.get('acked');
    if (acked === 'true') items = items.filter((a) => a.acked_by);
    if (acked === 'false') items = items.filter((a) => !a.acked_by);
    const type = q.get('type');
    if (type) items = items.filter((a) => a.type === type);
    const since = q.get('since');
    if (since) items = items.filter((a) => a.ts >= since);
    return json(paginate(items, q, 5));
  }
  m = path.match(/^\/alerts\/([^/]+)\/ack$/);
  if (m && method === 'POST') {
    const a = world.ack(m[1]!, 'manager@fleet');
    return a
      ? json({ id: a.id, acked_by: a.acked_by, acked_ts: a.acked_ts })
      : problem(404, 'Not found');
  }

  if (path === '/chargers') {
    let items = chargers;
    const depot = q.get('depot_id');
    if (depot) items = items.filter((c) => String(c.depot_id) === depot);
    return json(paginate(items, q));
  }
  if (path === '/tariffs/current') return json(tariff);

  if (path === '/reports/cost') {
    const to = q.get('to') ?? new Date().toISOString().slice(0, 10);
    const from = q.get('from') ?? new Date(Date.now() - 13 * 86_400_000).toISOString().slice(0, 10);
    return json({ days: makeCostDays(from, to) });
  }
  if (path === '/reports/battery-health') {
    return json(paginate(makeBattery(world.vehicles.map((v) => v.vehicle.vin)), q));
  }

  if (path === '/agent/query' && method === 'POST') {
    const b = await bodyOf(init);
    await delay(600);
    return json(world.agentQuery(String(b.question ?? '')));
  }
  m = path.match(/^\/agent\/actions\/([^/]+)\/(approve|reject)$/);
  if (m && method === 'POST') {
    const a = world.decide(m[1]!, m[2] as 'approve' | 'reject');
    return a ? json({ id: a.id, status: a.status }) : problem(404, 'Not found');
  }
  if (path === '/audit') return json(paginate(world.audit, q));

  return problem(404, 'Not found', `No mock for ${method} ${path}`);
}

/** Replace window.fetch with a router into the mock world (API paths only). */
export function installMockFetch() {
  const real = globalThis.fetch.bind(globalThis);
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const r = await mockRoute(url, init);
    return r ?? real(input, init);
  };
}
