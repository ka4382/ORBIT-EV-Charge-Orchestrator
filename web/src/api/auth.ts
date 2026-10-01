import type { Role } from './types';

const KEY = 'orbit.token';
const ROLE_KEY = 'orbit.session';

export interface Session {
  token: string;
  tenant_id: number;
  role: Role;
  sub: string;
}

let memoryToken: string | null = null;
let memorySession: Session | null = null;
const listeners = new Set<() => void>();

function safeStorage(): Storage | null {
  try {
    return typeof sessionStorage !== 'undefined' ? sessionStorage : null;
  } catch {
    return null;
  }
}

export function getToken(): string | null {
  if (memoryToken) return memoryToken;
  const s = safeStorage();
  const t = s?.getItem(KEY) ?? null;
  memoryToken = t;
  return t;
}

export function getSession(): Session | null {
  if (memorySession) return memorySession;
  const s = safeStorage();
  const raw = s?.getItem(ROLE_KEY);
  if (!raw) return null;
  try {
    memorySession = JSON.parse(raw) as Session;
    return memorySession;
  } catch {
    return null;
  }
}

export function setSession(session: Session | null) {
  memoryToken = session?.token ?? null;
  memorySession = session;
  const s = safeStorage();
  if (session) {
    s?.setItem(KEY, session.token);
    s?.setItem(ROLE_KEY, JSON.stringify(session));
  } else {
    s?.removeItem(KEY);
    s?.removeItem(ROLE_KEY);
  }
  listeners.forEach((l) => l());
}

export function clearSession() {
  setSession(null);
}

export function subscribeSession(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function isAuthenticated() {
  return !!getToken();
}

/** Decode a JWT payload without verifying (for display only). */
export function decodeJwt(token: string): Record<string, unknown> | null {
  try {
    const part = token.split('.')[1];
    if (!part) return null;
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}
