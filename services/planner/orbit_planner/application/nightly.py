"""Use case: plan every BEV of a tenant for a common departure (default 06:00 next day), then smooth each
depot's aggregate load under its grid capacity and persist all plans. Used by the demo and the cost report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..domain.depot import VehicleLoad, smooth_depot
from ..domain.models import PlanOutcome, PlanRequest
from .plan_vehicle import PlanVehicle, align_up, slots_between
from .ports import PlanRepository


@dataclass(slots=True)
class DepotReport:
    depot_id: int
    vehicles: int
    capacity_kw: float
    peak_kw_before: float
    peak_kw_after: float
    moves: int
    feasible: bool


@dataclass(slots=True)
class NightlyReport:
    tenant_id: int
    depart_ts: datetime
    planned: int = 0
    failed: int = 0
    infeasible: int = 0
    total_cost: float = 0.0
    baseline_cost: float = 0.0
    total_kwh: float = 0.0
    depots: list[DepotReport] = field(default_factory=list)
    failures: dict[str, int] = field(default_factory=dict)

    @property
    def savings(self) -> float:
        return round(self.baseline_cost - self.total_cost, 2)


def next_departure(now: datetime, local_time: time, tz: str) -> datetime:
    """The next occurrence of `local_time` in `tz` strictly after `now` (usually 06:00 tomorrow)."""
    zone = ZoneInfo(tz)
    local_now = now.astimezone(zone)
    candidate = local_now.replace(hour=local_time.hour, minute=local_time.minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


class NightlyPlanner:
    def __init__(
        self, repo: PlanRepository, planner: PlanVehicle, requested_by: str = "system:nightly"
    ) -> None:
        self.repo, self.planner, self.requested_by = repo, planner, requested_by

    async def run(
        self,
        tenant_id: int,
        depart_local: time = time(6, 0),
        target_soc: float = 90.0,
        tz: str = "Asia/Kolkata",
        persist: bool = True,
    ) -> NightlyReport:
        now = self.planner.clock()
        depart_ts = next_departure(now, depart_local, tz)
        report = NightlyReport(tenant_id=tenant_id, depart_ts=depart_ts)
        outcomes: list[PlanOutcome] = []
        for v in await self.repo.list_bevs(tenant_id):
            plan_id = await self.repo.create_plan(v.id, target_soc, depart_ts, self.requested_by)
            outcomes.append(
                await self.planner.compute(
                    PlanRequest(plan_id, v.vin, tenant_id, target_soc, depart_ts, vehicle_id=v.id)
                )
            )

        horizon_start = align_up(now, self.planner.config.slot_minutes)
        horizon = slots_between(horizon_start, depart_ts, self.planner.config.slot_minutes)
        by_depot: dict[int, list[PlanOutcome]] = {}
        for o in outcomes:
            if o.status == "READY" and o.depot_id is not None and o.schedule is not None:
                by_depot.setdefault(o.depot_id, []).append(o)
        for depot_id, group in by_depot.items():
            report.depots.append(self._smooth(depot_id, group, horizon_start, horizon))

        for o in outcomes:
            if o.status != "READY" or o.schedule is None:
                report.failed += 1
                report.failures[o.reason] = report.failures.get(o.reason, 0) + 1
            else:
                report.planned += 1
                report.infeasible += 0 if o.schedule.feasible else 1
                report.total_cost += o.schedule.total_cost
                report.baseline_cost += o.schedule.baseline_cost
                report.total_kwh += o.schedule.total_kwh
            if persist:
                await self.planner.persist(o)
        report.total_cost, report.baseline_cost = round(report.total_cost, 2), round(report.baseline_cost, 2)
        report.total_kwh = round(report.total_kwh, 2)
        return report

    def _smooth(
        self, depot_id: int, group: list[PlanOutcome], horizon_start: datetime, horizon: int
    ) -> DepotReport:
        """Project each schedule onto the depot's common horizon, smooth, then rebuild the schedules."""
        slot_minutes = self.planner.config.slot_minutes
        loads: list[VehicleLoad] = []
        for o in group:
            s = o.schedule
            assert s is not None and o.vehicle_id is not None
            offset = slots_between(horizon_start, s.arrival_ts, slot_minutes)
            kw = [0.0] * horizon
            prices = [s.prices[0] if s.prices else 0.0] * horizon
            for i, x in enumerate(s.kw_by_slot):
                if offset + i < horizon:
                    kw[offset + i] = x
                    prices[offset + i] = s.prices[i]
            loads.append(
                VehicleLoad(o.vehicle_id, offset, min(horizon, offset + s.n_slots), o.max_kw, kw, prices)
            )
        capacity = group[0].depot_capacity_kw
        res = smooth_depot(loads, capacity, horizon)
        for o, load in zip(group, loads, strict=True):
            s = o.schedule
            assert s is not None
            o.schedule = s.with_kw(load.kw[load.offset : load.offset + s.n_slots])
        return DepotReport(
            depot_id,
            len(group),
            capacity,
            round(res.peak_kw_before, 1),
            round(res.peak_kw_after, 1),
            res.moves,
            res.feasible,
        )
