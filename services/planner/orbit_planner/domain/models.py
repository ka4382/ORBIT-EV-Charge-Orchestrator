"""Value objects shared by the planner's use cases. Immutable, IO-free."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .schedule import Schedule, TariffSlot


@dataclass(frozen=True, slots=True)
class Charger:
    id: int
    name: str
    lat: float
    lon: float
    max_kw: float
    connector: str = "Type2"
    is_public: bool = False
    price_per_kwh: float | None = None  # None = depot tariff applies
    depot_id: int | None = None

    @property
    def is_dc(self) -> bool:
        return self.connector != "Type2"


@dataclass(frozen=True, slots=True)
class VehicleModel:
    id: int
    name: str
    powertrain: str  # ICE | HEV | PHEV | BEV
    battery_kwh: float | None
    nominal_range_km: float | None
    max_ac_kw: float | None
    max_dc_kw: float | None

    @property
    def is_electric(self) -> bool:
        return self.powertrain in ("BEV", "PHEV") and bool(self.battery_kwh)


@dataclass(frozen=True, slots=True)
class Depot:
    id: int
    lat: float
    lon: float
    grid_capacity_kw: float
    tariff: tuple[TariffSlot, ...] = ()
    tz: str = "Asia/Kolkata"


@dataclass(frozen=True, slots=True)
class Vehicle:
    id: int
    vin: str
    tenant_id: int
    fleet_id: int
    model: VehicleModel
    depot: Depot | None


@dataclass(frozen=True, slots=True)
class VehicleState:
    """Subset of the Redis `veh:{vin}` hash the planner needs."""

    soc_pct: float
    lat: float
    lon: float
    soh_pct: float | None = None
    ts: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """Payload of a `plans.requested` message (vehicle_id optional; resolved via VIN)."""

    plan_id: int
    vin: str
    tenant_id: int
    target_soc: float
    depart_ts: datetime
    vehicle_id: int | None = None


@dataclass(slots=True)
class PlanOutcome:
    """Result of planning one vehicle, ready to be persisted and published."""

    plan_id: int
    vin: str
    tenant_id: int
    status: str  # READY | FAILED
    vehicle_id: int | None = None
    reason: str = ""
    charger: Charger | None = None
    schedule: Schedule | None = None
    arrival_ts: datetime | None = None
    distance_km: float = 0.0
    depot_id: int | None = None  # set when charging at the fleet's own depot (smoothing applies)
    depot_capacity_kw: float = 0.0
    max_kw: float = 0.0  # usable power at the chosen charger (min of charger and vehicle)
    path: list[tuple[float, float]] = field(default_factory=list)

    @property
    def event(self) -> dict[str, object]:
        """Message published to Kafka `plans.ready` and Redis `events:{tenant_id}` (contracts/api.md)."""
        ev: dict[str, object] = {
            "type": "plan",
            "plan_id": self.plan_id,
            "vin": self.vin,
            "status": self.status,
        }
        if self.reason:
            ev["reason"] = self.reason
        return ev
