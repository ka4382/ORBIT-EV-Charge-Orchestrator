import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Page } from './types';
import { useCursorPagination } from './useCursorPagination';

function pages(all: number[], size: number) {
  return vi.fn(async (cursor: string | null): Promise<Page<number>> => {
    const off = Number(cursor ?? 0);
    const items = all.slice(off, off + size);
    return { items, next_cursor: off + size < all.length ? String(off + size) : null };
  });
}

describe('useCursorPagination', () => {
  it('loads the first page, follows next_cursor, and stops at the end', async () => {
    const fetcher = pages([1, 2, 3, 4, 5], 2);
    const { result } = renderHook(() => useCursorPagination(fetcher, 'k'));
    await waitFor(() => expect(result.current.items).toEqual([1, 2]));
    expect(result.current.hasMore).toBe(true);
    expect(fetcher).toHaveBeenLastCalledWith(null);

    await act(() => result.current.loadMore());
    expect(result.current.items).toEqual([1, 2, 3, 4]);
    expect(fetcher).toHaveBeenLastCalledWith('2');

    await act(() => result.current.loadMore());
    expect(result.current.items).toEqual([1, 2, 3, 4, 5]);
    expect(result.current.hasMore).toBe(false);

    await act(() => result.current.loadMore());
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('resets when the key changes and discards stale responses', async () => {
    let resolveFirst: ((p: Page<number>) => void) | null = null;
    const fetcher = vi.fn((cursor: string | null): Promise<Page<number>> => {
      if (fetcher.mock.calls.length === 1) {
        return new Promise((r) => {
          resolveFirst = r;
        });
      }
      return Promise.resolve({ items: [cursor === null ? 99 : 100], next_cursor: null });
    });
    const { result, rerender } = renderHook(({ k }) => useCursorPagination(fetcher, k), {
      initialProps: { k: 'a' },
    });
    rerender({ k: 'b' });
    await waitFor(() => expect(result.current.items).toEqual([99]));
    act(() => resolveFirst?.({ items: [1, 2], next_cursor: '2' }));
    await new Promise((r) => setTimeout(r, 10));
    expect(result.current.items).toEqual([99]);
  });

  it('surfaces errors and lets items be patched in place', async () => {
    const fetcher = vi.fn(async (): Promise<Page<{ id: number; ok: boolean }>> => ({
      items: [{ id: 1, ok: false }],
      next_cursor: null,
    }));
    const { result } = renderHook(() => useCursorPagination(fetcher, 'k'));
    await waitFor(() => expect(result.current.items).toHaveLength(1));
    act(() =>
      result.current.update(
        (i) => i.id === 1,
        (i) => ({ ...i, ok: true }),
      ),
    );
    expect(result.current.items[0]?.ok).toBe(true);

    const bad = vi.fn(async (): Promise<Page<number>> => {
      throw new Error('nope');
    });
    const r2 = renderHook(() => useCursorPagination(bad, 'x'));
    await waitFor(() => expect(r2.result.current.error?.message).toBe('nope'));
  });
});
