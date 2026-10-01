"""Decode one raw OEM payload into a canonical event, or classify why it cannot be.

Pure functions: the only inputs are bytes and a clock value, so the rules are unit-testable
without Kafka. Dedupe is a separate step (see `Deduper`) because it carries state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import orjson
from orbit_common.adapters import UnknownOemError, normalise
from orbit_common.events import CanonicalEvent
from orbit_common.streaming import CountMinSketch

REASON_MALFORMED = "malformed_json"
REASON_UNKNOWN_OEM = "unknown_oem"
REASON_INVALID = "invalid"
REASON_FUTURE_TS = "future_ts"
REASON_DUPLICATE = "duplicate"


@dataclass(slots=True)
class Accepted:
    event: CanonicalEvent


@dataclass(slots=True)
class Rejected:
    reason: str
    raw: bytes
    detail: str = ""

    def dlq_payload(self) -> bytes:
        """DLQ envelope `{"reason", "detail", "raw"}`; raw is decoded leniently so the envelope stays JSON."""
        return orjson.dumps(
            {"reason": self.reason, "detail": self.detail, "raw": self.raw.decode("utf-8", "replace")}
        )


Outcome = Accepted | Rejected


class Deduper(Protocol):
    def seen(self, key: bytes) -> bool: ...


def decode(raw: bytes, now: datetime, max_future_s: float = 300.0) -> Outcome:
    """JSON -> OEM adapter -> CanonicalEvent, rejecting malformed/unknown/invalid/future-dated payloads."""
    try:
        payload = orjson.loads(raw)
    except orjson.JSONDecodeError as e:
        return Rejected(REASON_MALFORMED, raw, str(e))
    if not isinstance(payload, dict):
        return Rejected(REASON_MALFORMED, raw, "payload is not a JSON object")
    try:
        event = normalise(payload)
    except UnknownOemError as e:
        return Rejected(REASON_UNKNOWN_OEM, raw, str(e))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return Rejected(REASON_INVALID, raw, f"{type(e).__name__}: {e}"[:500])
    if (event.ts - now).total_seconds() > max_future_s:
        return Rejected(REASON_FUTURE_TS, raw, f"ts={event.ts.isoformat()} > now+{max_future_s:.0f}s")
    return Accepted(event)


class DtcTracker:
    """Count-Min sketch over DTC codes seen on the stream; `top()` feeds the orbit_dtc_topk gauge."""

    def __init__(self, k: int = 20) -> None:
        self._sketch = CountMinSketch(k=k)

    def record(self, codes: list[str]) -> None:
        for code in codes:
            self._sketch.add(code)

    def top(self) -> list[tuple[str, int]]:
        return self._sketch.top()

    def estimate(self, code: str) -> int:
        return self._sketch.estimate(code)
