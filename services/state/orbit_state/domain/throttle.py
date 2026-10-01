"""Per-key publish throttle: at most one publish per key every `interval_s` seconds."""

from __future__ import annotations


class PublishThrottle:
    def __init__(self, interval_s: float = 2.0) -> None:
        self.interval_s = interval_s
        self._last: dict[str, float] = {}

    def allow(self, key: str, now: float) -> bool:
        last = self._last.get(key)
        if last is not None and now - last < self.interval_s:
            return False
        self._last[key] = now
        return True

    def forget(self, key: str) -> None:
        self._last.pop(key, None)

    def __len__(self) -> int:
        return len(self._last)
