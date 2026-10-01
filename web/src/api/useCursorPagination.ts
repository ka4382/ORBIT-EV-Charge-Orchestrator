import { useCallback, useEffect, useRef, useState } from 'react';
import type { Page } from './types';

export interface CursorState<T> {
  items: T[];
  nextCursor: string | null;
  loading: boolean;
  error: Error | null;
  hasMore: boolean;
  loadMore: () => Promise<void>;
  reset: () => void;
  /** Replace an item in place (e.g. after an ack). */
  update: (pred: (item: T) => boolean, patch: (item: T) => T) => void;
}

export type PageFetcher<T> = (cursor: string | null) => Promise<Page<T>>;

/**
 * Keyset pagination: concatenates pages, follows `next_cursor`, resets when
 * `key` changes. Requests are serialised; a stale response (from before a
 * reset) is discarded.
 */
export function useCursorPagination<T>(fetcher: PageFetcher<T>, key: string): CursorState<T> {
  const [items, setItems] = useState<T[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [exhausted, setExhausted] = useState(false);
  const gen = useRef(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const cursorRef = useRef<string | null>(null);
  const inFlight = useRef(false);

  const load = useCallback(async (cursor: string | null, replace: boolean) => {
    if (inFlight.current) return;
    inFlight.current = true;
    const g = gen.current;
    setLoading(true);
    setError(null);
    try {
      const page = await fetcherRef.current(cursor);
      if (g !== gen.current) return;
      setItems((prev) => (replace ? page.items : [...prev, ...page.items]));
      cursorRef.current = page.next_cursor;
      setNextCursor(page.next_cursor);
      setExhausted(page.next_cursor === null);
    } catch (e) {
      if (g !== gen.current) return;
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      inFlight.current = false;
      if (g === gen.current) setLoading(false);
    }
  }, []);

  const reset = useCallback(() => {
    gen.current += 1;
    inFlight.current = false;
    cursorRef.current = null;
    setItems([]);
    setNextCursor(null);
    setExhausted(false);
    void load(null, true);
  }, [load]);

  useEffect(() => {
    reset();
  }, [key, reset]);

  const loadMore = useCallback(async () => {
    if (exhausted || cursorRef.current === null) return;
    await load(cursorRef.current, false);
  }, [exhausted, load]);

  const update = useCallback((pred: (item: T) => boolean, patch: (item: T) => T) => {
    setItems((prev) => prev.map((it) => (pred(it) ? patch(it) : it)));
  }, []);

  return {
    items,
    nextCursor,
    loading,
    error,
    hasMore: !exhausted && nextCursor !== null,
    loadMore,
    reset,
    update,
  };
}
