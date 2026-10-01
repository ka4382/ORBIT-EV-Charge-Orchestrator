import { useEffect, useRef } from 'react';
import { getToken } from '../api/auth';
import type { WsMessage, WsSubscribe } from '../api/types';
import { useFleetStore } from './store';
import { isMock } from '../mock';

export interface SocketLike {
  send(data: string): void;
  close(): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
}

export type SocketFactory = (url: string) => SocketLike;

let factoryOverride: SocketFactory | null = null;
export function setSocketFactory(f: SocketFactory | null) {
  factoryOverride = f;
}

export function wsUrl(token: string): string {
  const base = (import.meta.env.VITE_API_BASE as string | undefined) ?? '';
  let origin: string;
  if (base.startsWith('http')) {
    origin = base.replace(/^http/, 'ws');
  } else {
    const loc =
      typeof window !== 'undefined' ? window.location : { protocol: 'http:', host: 'localhost' };
    origin = `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}${base}`;
  }
  return `${origin}/api/v1/ws/fleet?token=${encodeURIComponent(token)}`;
}

export function backoffDelay(attempt: number, base = 500, cap = 15_000): number {
  const exp = Math.min(cap, base * 2 ** attempt);
  return exp / 2 + Math.random() * (exp / 2);
}

/**
 * Connects to /api/v1/ws/fleet with reconnect + exponential backoff and feeds the
 * zustand fleet store. One socket per mounted hook; typically mounted once
 * in the console layout.
 */
export function useFleetSocket(subscribe?: WsSubscribe | null) {
  const applyMessage = useFleetStore((s) => s.applyMessage);
  const setStatus = useFleetStore((s) => s.setStatus);
  const subRef = useRef(subscribe);
  subRef.current = subscribe;
  const sockRef = useRef<SocketLike | null>(null);

  useEffect(() => {
    let attempt = 0;
    let closed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      const token = getToken();
      if (!token && !isMock) {
        setStatus('closed');
        return;
      }
      setStatus(attempt === 0 ? 'connecting' : 'reconnecting');
      const url = wsUrl(token ?? 'mock');
      let sock: SocketLike;
      try {
        sock = factoryOverride
          ? factoryOverride(url)
          : (new WebSocket(url) as unknown as SocketLike);
      } catch {
        schedule();
        return;
      }
      sockRef.current = sock;
      sock.onopen = () => {
        attempt = 0;
        setStatus('open');
        if (subRef.current) sock.send(JSON.stringify(subRef.current));
      };
      sock.onmessage = (ev) => {
        try {
          const msg = JSON.parse(String(ev.data)) as WsMessage;
          if (msg && typeof msg === 'object' && 'type' in msg) applyMessage(msg);
        } catch {
          /* ignore malformed frame */
        }
      };
      sock.onerror = () => {
        /* onclose follows */
      };
      sock.onclose = () => {
        sockRef.current = null;
        if (closed) return;
        schedule();
      };
    };

    const schedule = () => {
      if (closed) return;
      setStatus('reconnecting');
      const d = backoffDelay(attempt++);
      timer = setTimeout(connect, d);
    };

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      sockRef.current?.close();
      sockRef.current = null;
      setStatus('closed');
    };
  }, [applyMessage, setStatus]);

  useEffect(() => {
    if (subscribe && sockRef.current) {
      try {
        sockRef.current.send(JSON.stringify(subscribe));
      } catch {
        /* not open yet; sent on open */
      }
    }
  }, [subscribe]);
}
