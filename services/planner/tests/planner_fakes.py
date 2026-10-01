"""Shared fixtures: seed tariff (off-peak ₹6.5 22:00–06:00, normal ₹9, peak ₹12.5 18:00–22:00) and fakes."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from orbit_planner.domain.models import Charger, Depot, Vehicle, VehicleModel, VehicleState
from orbit_planner.domain.schedule import IST, Slot, TariffSlot

SEED_TARIFF: tuple[TariffSlot, ...] = tuple(
    TariffSlot(dow, s, e, p)
    for dow in range(7)
    for s, e, p in ((0, 360, 6.5), (360, 1080, 9.0), (1080, 1320, 12.5), (1320, 1440, 6.5))
)


def ist(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST).astimezone(UTC)


@pytest.fixture
def tariff() -> tuple[TariffSlot, ...]:
    return SEED_TARIFF


VAN = VehicleModel(1, "A-Van 60", "BEV", 60.0, 320.0, 11.0, 100.0)
DIESEL = VehicleModel(2, "B-Diesel", "ICE", None, None, None, None)
DEPOT = Depot(1, 13.08, 80.27, 60.0, SEED_TARIFF)  # tiny capacity so smoothing has work to do
DEPOT_CHARGERS = [
    Charger(11, "D1-C1", 13.0805, 80.2705, 22.0, "Type2", False, None, 1),
    Charger(12, "D1-C2", 13.0795, 80.2695, 22.0, "Type2", False, None, 1),
    Charger(13, "D1-C3", 13.0800, 80.2710, 60.0, "CCS2", False, None, 1),
]
PUBLIC = [Charger(90, "Chennai Public 1", 13.31, 80.51, 120.0, "CCS2", True, 16.0, None)]


def vehicle(i: int, model: VehicleModel = VAN, depot: Depot | None = DEPOT) -> Vehicle:
    return Vehicle(i, f"VIN{i:014d}", 1, 1, model, depot)


class FakeRepo:
    def __init__(self, vehicles: Sequence[Vehicle], chargers: Sequence[Charger] = DEPOT_CHARGERS) -> None:
        self.vehicles = {v.vin: v for v in vehicles}
        self.chargers = list(chargers)
        self.saved: dict[int, dict] = {}
        self.created: list[tuple] = []
        self._next = 1000
        self.missing_plans: set[int] = set()

    async def get_vehicle_by_vin(self, vin: str) -> Vehicle | None:
        return self.vehicles.get(vin)

    async def list_bevs(self, tenant_id: int) -> list[Vehicle]:
        return [v for v in self.vehicles.values() if v.tenant_id == tenant_id and v.model.powertrain == "BEV"]

    async def list_tenants(self) -> list[int]:
        return sorted({v.tenant_id for v in self.vehicles.values()})

    async def depot_chargers(self, depot_id: int) -> list[Charger]:
        return [c for c in self.chargers if c.depot_id == depot_id]

    async def create_plan(
        self, vehicle_id: int, target_soc: float, depart_ts: datetime, requested_by: str
    ) -> int:
        self._next += 1
        self.created.append((self._next, vehicle_id, target_soc, depart_ts, requested_by))
        return self._next

    async def save_plan(
        self,
        plan_id: int,
        status: str,
        charger_id: int | None,
        slots: Sequence[Slot],
        total_cost: float | None,
        total_kwh: float | None,
        baseline_cost: float | None,
        achievable_soc: float | None = None,
    ) -> bool:
        if plan_id in self.missing_plans:
            self.missing_plans.discard(plan_id)  # becomes visible on the next attempt
            return False
        self.saved[plan_id] = {
            "status": status,
            "charger_id": charger_id,
            "slots": list(slots),
            "total_cost": total_cost,
            "total_kwh": total_kwh,
            "baseline_cost": baseline_cost,
            "achievable_soc": achievable_soc,
        }
        return True


class FakeStore:
    def __init__(self, states: dict[str, VehicleState] | None = None, public: Sequence[Charger] = ()) -> None:
        self.states = dict(states or {})
        self.public = list(public)
        self.geo_calls = 0

    async def vehicle_state(self, vin: str) -> VehicleState | None:
        return self.states.get(vin)

    async def public_chargers_near(self, lat: float, lon: float, radius_km: float) -> list[Charger]:
        self.geo_calls += 1
        return list(self.public)


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[tuple[int, int | None, dict]] = []

    async def plan_ready(self, tenant_id: int, vehicle_id: int | None, event: dict[str, object]) -> None:
        self.events.append((tenant_id, vehicle_id, event))
