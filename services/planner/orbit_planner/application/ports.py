"""Ports (Protocols) the use cases depend on. Infrastructure adapters implement them; tests use fakes."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from ..domain.models import Charger, Vehicle, VehicleState
from ..domain.schedule import Slot


class PlanRepository(Protocol):
    """Postgres: vehicles, models, depots, tariffs, chargers, charging_plan / charging_slot."""

    async def get_vehicle_by_vin(self, vin: str) -> Vehicle | None: ...
    async def list_bevs(self, tenant_id: int) -> list[Vehicle]: ...
    async def list_tenants(self) -> list[int]: ...
    async def depot_chargers(self, depot_id: int) -> list[Charger]: ...
    async def create_plan(
        self, vehicle_id: int, target_soc: float, depart_ts: datetime, requested_by: str
    ) -> int: ...
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
        """Write slots + plan status.

        Returns False when the plan row is not visible (not committed yet, or rolled back).
        """
        ...


class StateStore(Protocol):
    """Redis: latest vehicle state and the public-charger geo index."""

    async def vehicle_state(self, vin: str) -> VehicleState | None: ...
    async def public_chargers_near(self, lat: float, lon: float, radius_km: float) -> list[Charger]: ...


class EventPublisher(Protocol):
    """Kafka `plans.ready` + Redis pub/sub `events:{tenant_id}`."""

    async def plan_ready(self, tenant_id: int, vehicle_id: int | None, event: dict[str, object]) -> None: ...
