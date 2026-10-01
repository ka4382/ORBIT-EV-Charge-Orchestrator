from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from orbit_planner.application.plan_vehicle import PlannerConfig, PlanVehicle, align_up, effective_kw
from orbit_planner.domain.graph import graph_for_city
from orbit_planner.domain.models import Charger, PlanRequest, VehicleState
from planner_fakes import (  # noqa: I001
    DEPOT,
    DEPOT_CHARGERS,
    DIESEL,
    PUBLIC,
    VAN,
    FakePublisher,
    FakeRepo,
    FakeStore,
    ist,
    vehicle,
)

NOW = ist(2026, 10, 1, 17, 3)
DEPART = ist(2026, 10, 2, 6, 0)


def make(vehicles, states=None, public=(), chargers=DEPOT_CHARGERS, **cfg):
    repo, store, pub = FakeRepo(vehicles, chargers), FakeStore(states, public), FakePublisher()
    planner = PlanVehicle(
        repo,
        store,
        pub,
        PlannerConfig(**cfg),
        clock=lambda: NOW,
        graph_factory=lambda lat, lon: graph_for_city(lat, lon, n=24, box_deg=0.6),
    )
    return repo, store, pub, planner


def request(v, target=80.0, plan_id=1) -> PlanRequest:
    return PlanRequest(plan_id, v.vin, v.tenant_id, target, DEPART)


async def test_happy_path_plans_at_depot_writes_and_publishes() -> None:
    v = vehicle(1)
    repo, store, pub, planner = make([v], {v.vin: VehicleState(45.0, 13.10, 80.30, 95.0)})
    out = await planner.execute(request(v))
    assert out.status == "READY" and out.reason == "" and out.vehicle_id == 1
    assert out.charger is not None and out.charger.id == 13  # 60 kW CCS2 depot charger preferred
    assert out.depot_id == DEPOT.id and out.depot_capacity_kw == DEPOT.grid_capacity_kw
    assert out.max_kw == 60 and out.distance_km > 3  # road distance ≥ 3 km straight-line
    assert out.arrival_ts >= NOW and out.arrival_ts.minute % 15 == 0
    assert len(out.path) >= 2
    s = out.schedule
    assert s is not None and s.feasible and s.final_soc >= 80
    assert all(sl.price_per_kwh == 6.5 for sl in s.slots)  # off-peak
    saved = repo.saved[1]
    assert saved["status"] == "READY" and saved["charger_id"] == 13 and saved["slots"] == s.slots
    assert saved["total_cost"] == s.total_cost and saved["baseline_cost"] > saved["total_cost"]
    assert pub.events == [(1, 1, {"type": "plan", "plan_id": 1, "vin": v.vin, "status": "READY"})]
    assert store.geo_calls == 0  # soc ≥ 15 → no public lookup


async def test_missing_state_assumes_soc_30_at_depot() -> None:
    v = vehicle(2)
    repo, store, pub, planner = make([v])
    out = await planner.execute(request(v, target=90))
    assert out.status == "READY" and out.distance_km < 0.2  # on-site: straight-line, no lattice hop
    assert out.schedule is not None and out.schedule.total_kwh == pytest.approx(
        36.0, abs=0.6
    )  # 30 → 90 of 60 kWh
    assert out.arrival_ts == align_up(NOW, 15)


async def test_low_soc_far_from_depot_uses_nearest_public_charger_with_its_price() -> None:
    v = vehicle(3)
    far = VehicleState(5.0, 13.30, 80.50)  # 5 % of 320 km = 16 km range, depot ≈ 35 km
    repo, store, pub, planner = make([v], {v.vin: far}, public=PUBLIC)
    out = await planner.execute(request(v, target=60))
    assert store.geo_calls == 1
    assert out.status == "READY" and out.charger == PUBLIC[0] and out.depot_id is None
    assert out.max_kw == 100  # min(charger 120, vehicle DC 100)
    s = out.schedule
    assert s is not None and s.feasible and {sl.price_per_kwh for sl in s.slots} == {16.0}
    assert s.total_cost == pytest.approx(s.total_kwh * 16.0, abs=0.05)


async def test_strand_risk_when_nothing_reachable() -> None:
    v = vehicle(4)
    repo, store, pub, planner = make([v], {v.vin: VehicleState(5.0, 13.30, 80.50)})
    out = await planner.execute(request(v))
    assert out.status == "FAILED" and "STRAND_RISK" in out.reason
    assert repo.saved[1]["status"] == "FAILED" and repo.saved[1]["slots"] == []
    assert pub.events[0][2]["status"] == "FAILED" and "reason" in pub.events[0][2]


async def test_failure_reasons() -> None:
    bev, ice, nodepot = vehicle(5), vehicle(6, DIESEL), vehicle(7, depot=None)
    repo, store, pub, planner = make([bev, ice, nodepot], {bev.vin: VehicleState(90.0, 13.08, 80.27)})
    unknown = await planner.compute(PlanRequest(9, "NOPE", 1, 80, DEPART, vehicle_id=42))
    assert unknown.status == "FAILED" and unknown.reason == "unknown vehicle" and unknown.vehicle_id == 42
    assert (await planner.compute(request(ice))).reason.startswith("vehicle is ICE")
    assert (await planner.compute(request(nodepot))).reason == "fleet has no depot"
    assert "already at 90%" in (await planner.compute(request(bev))).reason
    repo2, _, _, planner2 = make([bev], chargers=[])
    assert (await planner2.compute(request(bev))).reason == "no chargers configured"


async def test_infeasible_target_is_ready_with_reason() -> None:
    v = vehicle(8)
    repo, store, pub, planner = make([v], {v.vin: VehicleState(10.0, 13.08, 80.27)})
    out = await planner.execute(PlanRequest(1, v.vin, 1, 100.0, NOW + timedelta(hours=1)))
    assert out.status == "READY" and out.reason.startswith("target unreachable")
    assert out.schedule is not None and not out.schedule.feasible and out.schedule.total_kwh > 0
    assert pub.events[0][2]["reason"] == out.reason


def test_helpers() -> None:
    assert align_up(datetime(2026, 1, 1, 10, 0, 30, tzinfo=UTC), 15) == datetime(
        2026, 1, 1, 10, 15, tzinfo=UTC
    )
    assert align_up(datetime(2026, 1, 1, 10, 15, tzinfo=UTC), 15) == datetime(2026, 1, 1, 10, 15, tzinfo=UTC)
    ac = Charger(1, "ac", 0, 0, 22, "Type2")
    dc = Charger(2, "dc", 0, 0, 150, "CCS2")
    assert effective_kw(ac, VAN) == 11 and effective_kw(dc, VAN) == 100 and effective_kw(dc, DIESEL) == 150
    assert not ac.is_dc and dc.is_dc and not DIESEL.is_electric and VAN.is_electric


class _SharedListRepo(FakeRepo):
    """Returns the SAME list object every call, like a repository with an in-memory cache."""

    async def depot_chargers(self, depot_id: int) -> list[Charger]:
        return self.chargers


async def test_candidate_list_from_repository_is_not_mutated() -> None:
    """Regression: `candidates += public` appended public chargers to the repository's own list on every
    low-SoC plan, so fleet planning grew quadratically and depots 'acquired' public chargers."""
    v = vehicle(3)
    far = VehicleState(5.0, 13.30, 80.50)
    repo = _SharedListRepo([v], DEPOT_CHARGERS)
    planner = PlanVehicle(
        repo,
        FakeStore({v.vin: far}, PUBLIC),
        FakePublisher(),
        PlannerConfig(),
        clock=lambda: NOW,
        graph_factory=lambda lat, lon: graph_for_city(lat, lon, n=24, box_deg=0.6),
    )
    for i in range(3):
        await planner.compute(request(v, target=60, plan_id=i))
    assert repo.chargers == list(DEPOT_CHARGERS)


async def test_infeasible_plan_records_achievable_soc() -> None:
    v = vehicle(7)
    repo, store, pub, planner = make([v], {v.vin: VehicleState(10.0, 13.08, 80.27)})
    soon = NOW + timedelta(minutes=30)
    await planner.execute(PlanRequest(77, v.vin, v.tenant_id, 100.0, soon))
    saved = repo.saved[77]
    assert saved["status"] == "READY" and saved["achievable_soc"] is not None
    assert saved["achievable_soc"] < 100.0


async def test_persist_waits_for_plan_row_to_commit() -> None:
    """Regression: the API publishes before its INSERT commits; the planner must not drop the update."""
    v = vehicle(8)
    repo, store, pub, planner = make([v], {v.vin: VehicleState(30.0, 13.08, 80.27)})
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    planner.sleep = fake_sleep
    repo.missing_plans.add(88)
    await planner.execute(request(v, plan_id=88))
    assert slept == [0.2] and repo.saved[88]["status"] == "READY" and len(pub.events) == 1


async def test_persist_gives_up_when_plan_never_commits() -> None:
    v = vehicle(9)
    repo, store, pub, planner = make(
        [v], {v.vin: VehicleState(30.0, 13.08, 80.27)}, persist_retry_delays_s=(0.0,)
    )

    async def fake_sleep(s: float) -> None:
        repo.missing_plans.add(89)  # still not visible on the retry

    planner.sleep = fake_sleep
    repo.missing_plans.add(89)
    out = await planner.compute(request(v, plan_id=89))
    assert await planner.persist(out) is False
    assert 89 not in repo.saved and pub.events == []
