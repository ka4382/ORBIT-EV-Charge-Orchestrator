from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from orbit_common.events import CanonicalEvent
from orbit_common.vin import make_valid

VIN = "1HGCM82633A004352"
VIN2 = make_valid("JH4KA7532NC000000")
NOW = datetime(2026, 9, 25, 10, 15, 2, 120000, tzinfo=UTC)


def ev(seq: int = 1, vin: str = VIN, dt_s: float = 0.0, **over: Any) -> CanonicalEvent:
    base: dict[str, Any] = {
        "vin": vin,
        "ts": NOW + timedelta(seconds=dt_s),
        "ingest_ts": NOW + timedelta(seconds=dt_s + 0.2),
        "seq": seq,
        "tenant_id": 12,
        "oem": "OEM_A",
        "lat": 13.08,
        "lon": 80.27,
        "speed_kmh": 64.2,
        "soc_pct": 41.0,
        "soh_pct": 93.5,
        "odo_km": 18234.7,
        "ambient_c": 31.2,
        "dtc": ["P0301"],
        "evt": "HARSH_BRAKE",
        "charging_state": "NONE",
        "charging_kw": 0.0,
    }
    base.update(over)
    return CanonicalEvent(**base)


class FakeRepo:
    def __init__(self, fail: bool = False) -> None:
        self.batches: list[list[str]] = []
        self.fail = fail

    async def copy(self, rows: Sequence[str]) -> int:
        if self.fail:
            raise RuntimeError("pg down")
        self.batches.append(list(rows))
        return len(rows)

    @property
    def rows(self) -> list[str]:
        return [r for b in self.batches for r in b]


class FakeStore:
    def __init__(self, fail_keys: set[str] | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_substr = fail_keys or set()

    async def put(self, key: str, data: bytes) -> None:
        if any(s in key for s in self.fail_substr):
            raise RuntimeError("s3 down")
        self.objects[key] = data
