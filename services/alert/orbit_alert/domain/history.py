"""Per-VIN sliding-window state kept in memory by the engine (rules stay pure by receiving snapshots)."""

from __future__ import annotations

from dataclasses import dataclass


class SocRing:
    """Ring buffer of `buckets` × `bucket_s` seconds holding the max SoC observed per bucket.
    O(1) update, O(buckets) window scan (24 by default)."""

    def __init__(self, buckets: int = 24, bucket_s: int = 300) -> None:
        self.buckets, self.bucket_s = buckets, bucket_s
        self._slots: list[tuple[int, float] | None] = [None] * buckets

    def update(self, ts_epoch: float, soc: float) -> bool:
        """Record `soc` at `ts_epoch`. Returns True when a new bucket was opened (caller mirrors to Redis)."""
        b = int(ts_epoch // self.bucket_s) * self.bucket_s
        idx = (b // self.bucket_s) % self.buckets
        slot = self._slots[idx]
        if slot is None or slot[0] != b:
            if slot is not None and slot[0] > b:  # stale event for a bucket we already rotated past
                return False
            self._slots[idx] = (b, soc)
            return True
        if soc > slot[1]:
            self._slots[idx] = (b, soc)
        return False

    def max_in_window(self, ts_epoch: float, window_s: int = 7200) -> float | None:
        lo, hi = ts_epoch - window_s, ts_epoch
        best: float | None = None
        for slot in self._slots:
            if slot is not None and lo <= slot[0] <= hi and (best is None or slot[1] > best):
                best = slot[1]
        return best

    def entries(self) -> list[tuple[int, float]]:
        return sorted(s for s in self._slots if s is not None)


@dataclass(slots=True)
class ChargeSnapshot:
    charging_state: str
    charging_kw: float
    ts_epoch: float


class VinHistory:
    """Everything the engine remembers about one VIN between events."""

    __slots__ = ("charge", "soc")

    def __init__(self, buckets: int = 24, bucket_s: int = 300) -> None:
        self.soc = SocRing(buckets, bucket_s)
        self.charge: ChargeSnapshot | None = None
