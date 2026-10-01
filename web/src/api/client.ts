import { clearSession, getToken } from './auth';
import type { Problem } from './types';

export const API_BASE = `${(import.meta.env.VITE_API_BASE as string | undefined) ?? ''}/api/v1`;

export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem | null;
  readonly retryAfter: number | null;

  constructor(
    status: number,
    problem: Problem | null,
    message?: string,
    retryAfter: number | null = null,
  ) {
    super(message ?? problem?.detail ?? problem?.title ?? `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.problem = problem;
    this.retryAfter = retryAfter;
  }

  get isNotImplemented() {
    return this.status === 501;
  }
}

export type QueryValue = string | number | boolean | null | undefined;

export function buildQuery(params?: Record<string, QueryValue>): string {
  if (!params) return '';
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

/** Parse an RFC 7807 problem body if present; null otherwise. */
export async function parseProblem(res: Response): Promise<Problem | null> {
  const ct = res.headers.get('content-type') ?? '';
  if (!ct.includes('json')) {
    return null;
  }
  try {
    const body = (await res.json()) as Partial<Problem> & Record<string, unknown>;
    if (typeof body !== 'object' || body === null) return null;
    return {
      type: typeof body.type === 'string' ? body.type : 'about:blank',
      title: typeof body.title === 'string' ? body.title : res.statusText || `HTTP ${res.status}`,
      status: typeof body.status === 'number' ? body.status : res.status,
      detail: typeof body.detail === 'string' ? body.detail : undefined,
      instance: typeof body.instance === 'string' ? body.instance : undefined,
      trace_id: typeof body.trace_id === 'string' ? body.trace_id : undefined,
    };
  } catch {
    return null;
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
  auth?: boolean;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, signal, auth = true } = opts;
  const headers: Record<string, string> = { Accept: 'application/json, application/problem+json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const token = auth ? getToken() : null;
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}${buildQuery(query)}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (res.status === 401 && auth) {
    clearSession();
    onUnauthorized?.();
  }

  if (!res.ok) {
    const problem = await parseProblem(res);
    const ra = res.headers.get('retry-after');
    throw new ApiError(res.status, problem, undefined, ra ? Number(ra) : null);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, query?: Record<string, QueryValue>, signal?: AbortSignal) =>
    request<T>(path, { query, signal }),
  post: <T>(path: string, body?: unknown, opts: Omit<RequestOptions, 'method' | 'body'> = {}) =>
    request<T>(path, { ...opts, method: 'POST', body }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};
