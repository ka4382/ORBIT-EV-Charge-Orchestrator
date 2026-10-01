"""Ports the alert engine depends on (implemented by infrastructure/ and by test fakes)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from orbit_alert.domain.alerts import Alert


@dataclass(frozen=True, slots=True)
class VehicleInfo:
    vehicle_id: int
    nominal_range_km: float | None


@dataclass(frozen=True, slots=True)
class GeoQuery:
    lat: float
    lon: float
    radius_km: float


@dataclass(frozen=True, slots=True)
class SocSample:
    vin: str
    ts_epoch: int
    soc: float


class VehicleLookup(Protocol):
    async def lookup(self, vins: Iterable[str]) -> dict[str, VehicleInfo]: ...


class ChargerIndex(Protocol):
    async def reachable(self, queries: Sequence[GeoQuery]) -> list[bool]: ...


class AlertDeduper(Protocol):
    async def claim(self, keys: Sequence[str]) -> list[bool]:
        """SET NX EX for every key; True where this call won the key (alert should be emitted)."""
        ...


class AlertRepository(Protocol):
    async def insert(self, alerts: Sequence[Alert]) -> list[int]:
        """Insert alerts (all with vehicle_id set) and return their ids in order."""
        ...


class AlertPublisher(Protocol):
    async def emit(self, alerts: Sequence[Alert]) -> None:
        """Produce to the `alerts` topic; returns after broker acks."""
        ...


class EventBus(Protocol):
    async def publish(self, alerts: Sequence[Alert], soc_samples: Sequence[SocSample]) -> None:
        """PUBLISH alert messages to events:{tenant} and mirror SoC samples to soc:{vin}."""
        ...


class ConsumerPort(Protocol):
    async def getmany(self, *, timeout_ms: int, max_records: int) -> dict[Any, list[Any]]: ...
    async def commit(self, offsets: dict[Any, int]) -> None: ...
