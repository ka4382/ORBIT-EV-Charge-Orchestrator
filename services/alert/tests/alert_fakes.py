from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from orbit_alert.application.ports import GeoQuery, SocSample, VehicleInfo
from orbit_alert.domain.alerts import Alert
from orbit_common.events import CanonicalEvent
from orbit_common.vin import make_valid

VIN = "1HGCM82633A004352"
VIN2 = make_valid("JH4KA7532NC000000")
UNKNOWN_VIN = make_valid("WBA3A5C50CF000000")
NOW = datetime(2026, 9, 25, 10, 15, 0, tzinfo=UTC)


def ev(seq: int = 1, vin: str = VIN, dt_s: float = 0.0, **over: Any) -> CanonicalEvent:
    base: dict[str, Any] = {
        "vin": vin,
        "ts": NOW + timedelta(seconds=dt_s),
        "seq": seq,
        "tenant_id": 12,
        "oem": "OEM_A",
        "lat": 13.08,
        "lon": 80.27,
        "speed_kmh": 0.0,
        "soc_pct": 50.0,
        "soh_pct": 95.0,
        "odo_km": 100.0,
        "dtc": [],
        "charging_state": "NONE",
        "charging_kw": 0.0,
    }
    base.update(over)
    return CanonicalEvent(**base)


class FakeLookup:
    def __init__(self, table: dict[str, VehicleInfo] | None = None) -> None:
        self.table = table if table is not None else {VIN: VehicleInfo(1, 300.0), VIN2: VehicleInfo(2, None)}
        self.calls: list[list[str]] = []

    async def lookup(self, vins: Iterable[str]) -> dict[str, VehicleInfo]:
        vins = list(vins)
        self.calls.append(vins)
        return {v: self.table[v] for v in vins if v in self.table}


class FakeChargers:
    def __init__(self, reachable: bool = True) -> None:
        self.reachable_default = reachable
        self.queries: list[GeoQuery] = []

    async def reachable(self, queries: Sequence[GeoQuery]) -> list[bool]:
        self.queries.extend(queries)
        return [self.reachable_default for _ in queries]


class FakeDeduper:
    def __init__(self) -> None:
        self.claimed: set[str] = set()

    async def claim(self, keys: Sequence[str]) -> list[bool]:
        out = []
        for k in keys:
            out.append(k not in self.claimed)
            self.claimed.add(k)
        return out


class FakeRepo:
    def __init__(self) -> None:
        self.rows: list[Alert] = []

    async def insert(self, alerts: Sequence[Alert]) -> list[int]:
        ids = []
        for a in alerts:
            assert a.vehicle_id is not None
            self.rows.append(a)
            ids.append(len(self.rows))
        return ids


class FakePublisher:
    def __init__(self) -> None:
        self.emitted: list[Alert] = []

    async def emit(self, alerts: Sequence[Alert]) -> None:
        self.emitted.extend(alerts)


class FakeBus:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []
        self.samples: list[SocSample] = []

    async def publish(self, alerts: Sequence[Alert], soc_samples: Sequence[SocSample]) -> None:
        self.alerts.extend(alerts)
        self.samples.extend(soc_samples)
