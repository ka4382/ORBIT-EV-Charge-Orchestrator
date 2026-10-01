"""Repository ports. Every implementation is constructed per request, already scoped to one tenant.
Rows are plain dicts (column name → value) so the presentation layer can validate them into schemas."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

Row = dict[str, Any]


class FleetRepository(Protocol):
    async def find(self) -> list[Row]: ...
    async def vins(self, fleet_id: int) -> set[str]: ...


class VehicleRepository(Protocol):
    async def find(self, *, fleet_id: int | None, after_id: int | None, limit: int) -> list[Row]: ...
    async def get_by_vin(self, vin: str) -> Row | None: ...
    async def count(self) -> int: ...
    async def depot_location(self, vehicle_id: int) -> tuple[float, float] | None: ...
    async def find_by_vins(self, vins: Sequence[str]) -> list[Row]:
        """`{id, vin}` of the given VINs that belong to this tenant (unknown / foreign VINs are absent)."""
        ...

    async def telemetry(
        self,
        vin: str,
        *,
        from_ts: datetime | None,
        to_ts: datetime | None,
        cursor: tuple[datetime, int] | None,
        limit: int,
    ) -> list[Row]: ...


class PlanRepository(Protocol):
    async def create(
        self, *, vehicle_id: int, target_soc: float, depart_ts: datetime, requested_by: str
    ) -> int: ...
    async def get(self, plan_id: int) -> Row | None: ...
    async def slots(self, plan_id: int) -> list[Row]: ...
    async def totals_today(self) -> tuple[float, float]:
        """(planned cost, savings vs charge-on-arrival) for plans created today, Asia/Kolkata."""
        ...

    async def find(
        self, *, vin: str | None, status: str | None, cursor: tuple[datetime, int] | None, limit: int
    ) -> list[Row]: ...


class AlertRepository(Protocol):
    async def find(
        self,
        *,
        severity: str | None,
        since: datetime | None,
        acked: bool | None,
        cursor: tuple[datetime, int] | None,
        limit: int,
    ) -> list[Row]: ...
    async def ack(self, alert_id: int, acked_by: str) -> Row | None: ...
    async def count_open(self) -> int: ...


class ChargerRepository(Protocol):
    async def find(
        self, *, depot_id: int | None, public: bool | None, after_id: int | None, limit: int
    ) -> list[Row]: ...


class TariffRepository(Protocol):
    async def current(self) -> Row | None: ...


class ReportRepository(Protocol):
    async def cost_by_day(self, from_ts: datetime, to_ts: datetime) -> list[Row]: ...
    async def battery_health(self, *, after_vin: str | None, limit: int) -> list[Row]: ...


class AgentRepository(Protocol):
    async def decide(self, action_id: int, *, status: str, decided_by: str) -> Row | None: ...
    async def get(self, action_id: int) -> Row | None: ...
    async def propose(self, *, action: str, params: dict[str, Any], rationale: str, proposed_by: str) -> Row:
        """Insert a PENDING agent_action (the only write the agent itself can make)."""
        ...

    async def set_status(self, action_id: int, *, status: str, from_status: str) -> Row | None:
        """Move an action from `from_status` to `status` (e.g. APPROVED -> EXECUTED); None if not in it."""
        ...


class PrivacyRepository(Protocol):
    async def erase_driver(self, driver_id: int) -> bool: ...


class AuditRepository(Protocol):
    async def insert(
        self, *, actor_id: str, action: str, resource: str, details: dict[str, Any], actor_type: str = "user"
    ) -> int: ...
    async def find(self, *, cursor: tuple[datetime, int] | None, limit: int) -> list[Row]: ...


@dataclass(slots=True)
class Repos:
    """Bundle handed to routers by `get_repos`; all members share one tenant-scoped transaction."""

    tenant_id: int
    fleets: FleetRepository
    vehicles: VehicleRepository
    plans: PlanRepository
    alerts: AlertRepository
    chargers: ChargerRepository
    tariffs: TariffRepository
    reports: ReportRepository
    agent: AgentRepository
    privacy: PrivacyRepository
    audit: AuditRepository
