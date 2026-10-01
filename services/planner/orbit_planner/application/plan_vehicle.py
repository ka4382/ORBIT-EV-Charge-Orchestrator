"""Use case: compute, persist and publish a charging plan for one vehicle."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ..domain.graph import RoadGraph, graph_for_city, nearest_reachable
from ..domain.models import Charger, Depot, PlanOutcome, PlanRequest, Vehicle, VehicleModel, VehicleState
from ..domain.schedule import ScheduleRequest, flat_tariff, optimise
from .ports import EventPublisher, PlanRepository, StateStore

GraphFactory = Callable[[float, float], RoadGraph]


@dataclass(frozen=True, slots=True)
class PlannerConfig:
    assumed_soc: float = 30.0  # when Redis has no state: vehicle is at the depot with this SoC
    low_soc_public: float = 15.0  # below this, public chargers are candidates too
    public_radius_km: float = 25.0
    travel_speed_kmh: float = 30.0  # urban average to estimate arrival at the charger
    slot_minutes: int = 15
    lambda_above_80: float = 0.0
    lambda_dc: float = 0.0
    fallback_range_km: float = 250.0
    graph_n: int = 60
    graph_box_deg: float = 0.6
    persist_retry_delays_s: tuple[float, ...] = (0.2, 0.5, 1.0, 2.0, 4.0)
    max_candidates: int = 12  # nearest chargers considered; paths are only rebuilt for these


def align_up(ts: datetime, minutes: int) -> datetime:
    """Round `ts` up to the next multiple of `minutes` (slot boundary); an exact boundary is unchanged."""
    utc = ts.astimezone(UTC)
    base = utc.replace(second=0, microsecond=0)
    if base != utc:
        base += timedelta(minutes=1)
    rem = base.minute % minutes
    return base if rem == 0 else base + timedelta(minutes=minutes - rem)


def effective_kw(charger: Charger, model: VehicleModel) -> float:
    """Charging power is bounded by both the charger and the vehicle's on-board AC / DC limit."""
    limit = model.max_dc_kw if charger.is_dc else model.max_ac_kw
    return min(charger.max_kw, limit) if limit else charger.max_kw


def remaining_range_km(state: VehicleState, model: VehicleModel, fallback_range_km: float) -> float:
    nominal = model.nominal_range_km or fallback_range_km
    soh = (state.soh_pct or 100.0) / 100.0
    return nominal * state.soc_pct / 100.0 * soh


class PlanVehicle:
    def __init__(
        self,
        repo: PlanRepository,
        store: StateStore,
        publisher: EventPublisher,
        config: PlannerConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        graph_factory: GraphFactory | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.repo, self.store, self.publisher = repo, store, publisher
        self.sleep = sleep
        self.config = config or PlannerConfig()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.graph_factory = graph_factory or (
            lambda lat, lon: graph_for_city(lat, lon, self.config.graph_n, self.config.graph_box_deg)
        )

    async def execute(self, req: PlanRequest) -> PlanOutcome:
        outcome = await self.compute(req)
        await self.persist(outcome)
        return outcome

    async def compute(self, req: PlanRequest) -> PlanOutcome:
        """Pure decision step: no writes. Returns a READY outcome with a schedule, or FAILED with a reason."""
        vehicle = await self.repo.get_vehicle_by_vin(req.vin)
        if vehicle is None:
            return PlanOutcome(
                req.plan_id, req.vin, req.tenant_id, "FAILED", req.vehicle_id, "unknown vehicle"
            )
        fail = lambda reason: PlanOutcome(req.plan_id, req.vin, req.tenant_id, "FAILED", vehicle.id, reason)  # noqa: E731
        if not vehicle.model.is_electric or vehicle.model.battery_kwh is None:
            return fail(f"vehicle is {vehicle.model.powertrain}; nothing to plan")
        if vehicle.depot is None:
            return fail("fleet has no depot")
        depot = vehicle.depot
        state = await self.store.vehicle_state(req.vin) or VehicleState(
            self.config.assumed_soc, depot.lat, depot.lon
        )
        if state.soc_pct >= req.target_soc:
            return fail(f"already at {state.soc_pct:.0f}% ≥ target {req.target_soc:.0f}%")

        # copy: never mutate a list the repository may cache or share between plans
        candidates = list(await self.repo.depot_chargers(depot.id))
        if state.soc_pct < self.config.low_soc_public:
            candidates += await self.store.public_chargers_near(
                state.lat, state.lon, self.config.public_radius_km
            )
        if not candidates:
            return fail("no chargers configured")

        graph = self.graph_factory(depot.lat, depot.lon)
        range_km = remaining_range_km(state, vehicle.model, self.config.fallback_range_km)
        reachable = nearest_reachable(
            graph,
            (state.lat, state.lon),
            candidates,
            range_km,
            k=min(len(candidates), self.config.max_candidates),
        )
        chosen = self._choose(reachable, vehicle, depot)
        if chosen is None:
            return fail(f"no charger reachable within {range_km:.0f} km (STRAND_RISK)")
        charger, distance_km, path = chosen

        now = self.clock()
        travel = timedelta(hours=distance_km / self.config.travel_speed_kmh)
        arrival = align_up(now + travel, self.config.slot_minutes)
        tariff = flat_tariff(charger.price_per_kwh) if charger.price_per_kwh is not None else depot.tariff
        max_kw = effective_kw(charger, vehicle.model)
        schedule = optimise(
            ScheduleRequest(
                soc_now=state.soc_pct,
                target_soc=req.target_soc,
                battery_kwh=vehicle.model.battery_kwh,
                max_kw=max_kw,
                arrival_ts=arrival,
                depart_ts=req.depart_ts,
                tariff=tariff,
                tz=ZoneInfo(depot.tz),
                slot_minutes=self.config.slot_minutes,
                lambda_above_80=self.config.lambda_above_80,
                lambda_dc=self.config.lambda_dc,
                is_dc=charger.is_dc,
            )
        )
        reason = (
            "" if schedule.feasible else f"target unreachable before departure; reaches {schedule.final_soc}%"
        )
        at_depot = charger.depot_id == depot.id
        return PlanOutcome(
            req.plan_id,
            req.vin,
            req.tenant_id,
            "READY",
            vehicle.id,
            reason,
            charger,
            schedule,
            arrival,
            round(distance_km, 2),
            depot.id if at_depot else None,
            depot.grid_capacity_kw if at_depot else 0.0,
            max_kw,
            list(path),
        )

    @staticmethod
    def _choose(reachable: list, vehicle: Vehicle, depot: Depot) -> tuple[Charger, float, tuple] | None:
        """Depot charger with the highest usable kW if any is reachable, else the nearest public one."""
        best: tuple[float, float, Charger, tuple] | None = None
        for rc in reachable:
            ch: Charger = rc.charger
            if ch.depot_id != depot.id:
                continue
            key = (-effective_kw(ch, vehicle.model), rc.distance_km)
            if best is None or key < (best[0], best[1]):
                best = (key[0], key[1], ch, rc.path)
        if best is not None:
            return best[2], best[1], best[3]
        for rc in reachable:  # already sorted by road distance
            if rc.charger.is_public:
                return rc.charger, rc.distance_km, rc.path
        return None

    async def persist(self, outcome: PlanOutcome) -> bool:
        """Write charging_slot rows + charging_plan status, then notify api (Kafka + Redis pub/sub).

        The API publishes `plans.requested` inside the transaction that inserts the plan, so the planner can
        receive a request before that INSERT commits. An UPDATE would then silently match nothing and the
        plan would stay PENDING forever. So: retry with backoff while the row is not visible; if it never
        appears the API rolled back (e.g. audit write failed) and the request is dropped. Returns whether
        the plan was written."""
        for delay in (*self.config.persist_retry_delays_s, None):
            if await self._save(outcome):
                await self.publisher.plan_ready(outcome.tenant_id, outcome.vehicle_id, outcome.event)
                return True
            if delay is None:
                return False
            await self.sleep(delay)
        return False  # pragma: no cover

    async def _save(self, outcome: PlanOutcome) -> bool:
        sched = outcome.schedule
        if sched is not None and outcome.charger is not None:
            return await self.repo.save_plan(
                outcome.plan_id,
                outcome.status,
                outcome.charger.id,
                sched.slots,
                sched.total_cost,
                sched.total_kwh,
                sched.baseline_cost,
                float(sched.final_soc),
            )
        return await self.repo.save_plan(outcome.plan_id, outcome.status, None, [], None, None, None)


def slots_between(start: datetime, end: datetime, slot_minutes: int) -> int:
    return max(0, math.floor((end - start).total_seconds() / 60.0 / slot_minutes))
