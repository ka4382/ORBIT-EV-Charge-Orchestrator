"""Streaming data structures: rolling Bloom filter (dedupe) and Count-Min sketch (top-K)."""

from __future__ import annotations

import heapq
import math
import time
from collections import Counter

import mmh3
import numpy as np


class BloomFilter:
    """Standard Bloom filter over bytes keys. k probe positions by double hashing one 128-bit murmur3.

    Bits live in a plain `bytearray`: indexing it from Python is ~10x cheaper than numpy scalar indexing,
    which matters at 100K events/s (≈1.5 us per `add` instead of ≈6 us).
    """

    def __init__(self, capacity: int, fp_rate: float = 0.001) -> None:
        self.capacity = capacity
        self.m = max(8, int(-capacity * math.log(fp_rate) / (math.log(2) ** 2)))
        self.k = max(1, int(round(self.m / capacity * math.log(2))))
        self._bits = bytearray((self.m + 7) // 8)
        self.count = 0

    def _positions(self, key: bytes) -> list[int]:
        h1, h2 = mmh3.hash64(key, signed=False)
        m = self.m
        return [(h1 + i * h2) % m for i in range(self.k)]

    def add(self, key: bytes) -> bool:
        """Add key. Returns True if it was (probably) already present."""
        return self.add_positions(self._positions(key))

    def contains_positions(self, positions: list[int]) -> bool:
        bits = self._bits
        return all(bits[p >> 3] & (1 << (p & 7)) for p in positions)

    def add_positions(self, positions: list[int]) -> bool:
        bits = self._bits
        present = True
        for p in positions:
            byte, mask = p >> 3, 1 << (p & 7)
            if not bits[byte] & mask:
                present = False
                bits[byte] |= mask
        if not present:
            self.count += 1
        return present

    def __contains__(self, key: bytes) -> bool:
        bits = self._bits
        return all(bits[p >> 3] & (1 << (p & 7)) for p in self._positions(key))


class RollingBloom:
    """Two-generation Bloom: rotates every `window_s` seconds so memory is bounded and old keys age out.
    A key is a duplicate if it is in the current OR previous generation. O(k) per op."""

    def __init__(self, capacity_per_window: int, window_s: float = 600.0, fp_rate: float = 0.001) -> None:
        self.capacity = capacity_per_window
        self.window_s = window_s
        self.fp_rate = fp_rate
        self.current = BloomFilter(capacity_per_window, fp_rate)
        self.previous = BloomFilter(capacity_per_window, fp_rate)
        self.rotated_at = time.monotonic()

    def _maybe_rotate(self) -> None:
        now = time.monotonic()
        if now - self.rotated_at >= self.window_s or self.current.count >= self.capacity:
            self.previous, self.current = self.current, BloomFilter(self.capacity, self.fp_rate)
            self.rotated_at = now

    def seen(self, key: bytes) -> bool:
        self._maybe_rotate()
        # both generations share m and k, so the probe positions are hashed once and reused
        pos = self.current._positions(key)
        if self.previous.contains_positions(pos):
            return True
        return self.current.add_positions(pos)


class CountMinSketch:
    """Count-Min sketch with a small heap for top-K. width×depth counters, O(depth) per update."""

    def __init__(self, width: int = 2048, depth: int = 4, k: int = 20) -> None:
        self.width, self.depth, self.k = width, depth, k
        self._table = np.zeros((depth, width), dtype=np.int64)
        self._top: dict[str, int] = {}

    def add(self, key: str, n: int = 1) -> int:
        b = key.encode()
        est = None
        for d in range(self.depth):
            idx = mmh3.hash(b, seed=d, signed=False) % self.width
            self._table[d, idx] += n
            v = int(self._table[d, idx])
            est = v if est is None else min(est, v)
        assert est is not None
        self._top[key] = est
        if len(self._top) > self.k * 4:  # prune occasionally
            self._top = dict(heapq.nlargest(self.k * 2, self._top.items(), key=lambda kv: kv[1]))
        return est

    def estimate(self, key: str) -> int:
        b = key.encode()
        return min(
            int(self._table[d, mmh3.hash(b, seed=d, signed=False) % self.width]) for d in range(self.depth)
        )

    def top(self) -> list[tuple[str, int]]:
        return Counter(self._top).most_common(self.k)
