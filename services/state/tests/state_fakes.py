from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from orbit_common.events import CanonicalEvent
from orbit_state.application.ports import WriteBatch
from orbit_state.domain.state import LastSeen

VIN = "1HGCM82633A004352"
VIN2 = "JH4KA7532NC000000"  # fixed below to a valid check digit
NOW = datetime(2026, 9, 25, 10, 15, 2, tzinfo=UTC)


def _fix(v: str) -> str:
    from orbit_common.vin import make_valid

    return make_valid(v)


VIN2 = _fix(VIN2)


def ev(seq: int = 1, vin: str = VIN, dt_s: float = 0.0, **over: Any) -> CanonicalEvent:
    base: dict[str, Any] = {
        "vin": vin,
        "ts": NOW + timedelta(seconds=dt_s),
        "seq": seq,
        "tenant_id": 12,
        "oem": "OEM_A",
        "lat": 21.17,
        "lon": 72.83,
        "speed_kmh": 0.0,
        "soc_pct": 41.0,
        "soh_pct": 93.5,
        "odo_km": 100.0,
        "dtc": [],
        "charging_state": "NONE",
        "charging_kw": 0.0,
    }
    base.update(over)
    return CanonicalEvent(**base)


class FakeStore:
    def __init__(self, existing: dict[str, LastSeen] | None = None) -> None:
        self.existing = existing or {}
        self.batches: list[WriteBatch] = []
        self.fetches: list[set[str]] = []

    async def fetch_last_seen(self, vins: Iterable[str]) -> dict[str, LastSeen]:
        vins = set(vins)
        self.fetches.append(vins)
        return {v: ls for v, ls in self.existing.items() if v in vins}

    async def write(self, batch: WriteBatch) -> None:
        self.batches.append(batch)

    @property
    def states(self):
        return [s for b in self.batches for s in b.states]

    @property
    def summaries(self):
        return [s for b in self.batches for s in b.summaries]
