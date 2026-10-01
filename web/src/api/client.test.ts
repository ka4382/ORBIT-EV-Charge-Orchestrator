import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, buildQuery, parseProblem, request, setUnauthorizedHandler } from './client';
import { setSession, getToken } from './auth';

function res(
  status: number,
  body: unknown,
  ct = 'application/problem+json',
  headers: Record<string, string> = {},
) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'content-type': ct, ...headers },
  });
}

describe('api client', () => {
  beforeEach(() => {
    setSession({ token: 'tok', tenant_id: 1, role: 'manager', sub: 'me' });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    setSession(null);
    setUnauthorizedHandler(null);
  });

  it('parses RFC 7807 problem bodies into ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      res(422, {
        type: 'https://orbit/errors/validation',
        title: 'Validation failed',
        status: 422,
        detail: 'target_soc must be <= 100',
        trace_id: 'abc',
      }),
    );
    await expect(request('/plans', { method: 'POST', body: {} })).rejects.toMatchObject({
      name: 'ApiError',
      status: 422,
      message: 'target_soc must be <= 100',
      problem: {
        title: 'Validation failed',
        trace_id: 'abc',
        type: 'https://orbit/errors/validation',
      },
    });
  });

  it('falls back to a generic message when the body is not JSON', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('<html>bad gateway</html>', {
        status: 502,
        headers: { 'content-type': 'text/html' },
      }),
    );
    const err = await request('/fleets').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).problem).toBeNull();
    expect((err as ApiError).message).toBe('HTTP 502');
  });

  it('exposes Retry-After on 429', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      res(429, { title: 'Too many requests', status: 429 }, 'application/problem+json', {
        'retry-after': '7',
      }),
    );
    const err = (await request('/fleets').catch((e: unknown) => e)) as ApiError;
    expect(err.retryAfter).toBe(7);
  });

  it('adds the bearer header and clears the session on 401', async () => {
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(res(401, { title: 'Unauthorized', status: 401 }));
    const onUnauth = vi.fn();
    setUnauthorizedHandler(onUnauth);
    await expect(request('/me')).rejects.toBeInstanceOf(ApiError);
    const init = spy.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer tok');
    expect(onUnauth).toHaveBeenCalled();
    expect(getToken()).toBeNull();
  });

  it('returns parsed JSON on success and encodes query params', async () => {
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(res(200, { items: [], next_cursor: null }, 'application/json'));
    const out = await request<{ items: unknown[] }>('/vehicles', {
      query: { limit: 50, cursor: null, status: 'AT_RISK' },
    });
    expect(out.items).toEqual([]);
    expect(String(spy.mock.calls[0]?.[0])).toBe('/api/v1/vehicles?limit=50&status=AT_RISK');
  });

  it('parseProblem tolerates partial bodies', async () => {
    const p = await parseProblem(res(500, { detail: 'boom' }, 'application/json'));
    expect(p).toMatchObject({ type: 'about:blank', status: 500, detail: 'boom' });
  });

  it('buildQuery drops empty values', () => {
    expect(buildQuery({ a: 1, b: '', c: undefined, d: false })).toBe('?a=1&d=false');
    expect(buildQuery()).toBe('');
  });

  it('501 is flagged as not implemented', () => {
    expect(new ApiError(501, null).isNotImplemented).toBe(true);
  });
});
