from __future__ import annotations

from datetime import UTC, datetime, time

from orbit_planner.application.nightly import NightlyPlanner, next_departure
from orbit_planner.application.plan_vehicle import PlannerConfig, PlanVehicle
from orbit_planner.domain.graph import graph_for_city
from orbit_planner.domain.models import VehicleState
from planner_fakes import DEPOT, DIESEL, FakePublisher, FakeRepo, FakeStore, ist, vehicle

NOW = ist(2026, 10, 1, 21, 40)


def test_next_departure_is_tomorrow_when_time_has_passed() -> None:
    d = next_departure(NOW, time(6, 0), "Asia/Kolkata")
    assert d == ist(2026, 10, 2, 6, 0)
    early = next_departure(ist(2026, 10, 1, 3, 0), time(6, 0), "Asia/Kolkata")
    assert early == ist(2026, 10, 1, 6, 0)
    assert next_departure(datetime(2026, 1, 1, tzinfo=UTC), time(6, 0), "UTC") == datetime(
        2026, 1, 1, 6, tzinfo=UTC
    )


async def test_nightly_plans_all_bevs_and_smooths_depot_under_capacity() -> None:
    vans = [vehicle(i) for i in range(1, 7)]  # 6 × 60 kW DC at a 60 kW depot → must smooth
    states = {v.vin: VehicleState(40.0, 13.085, 80.275) for v in vans}
    repo = FakeRepo(vans + [vehicle(99, DIESEL)])
    store, pub = FakeStore(states), FakePublisher()
    planner = PlanVehicle(
        repo,
        store,
        pub,
        PlannerConfig(),
        clock=lambda: NOW,
        graph_factory=lambda lat, lon: graph_for_city(lat, lon, n=24, box_deg=0.6),
    )
    report = await NightlyPlanner(repo, planner).run(1, target_soc=90.0)

    assert report.depart_ts == ist(2026, 10, 2, 6, 0)
    assert report.planned == 6 and report.failed == 0 and report.infeasible == 0
    assert len(repo.created) == 6 and all(c[4] == "system:nightly" for c in repo.created)  # ICE skipped
    assert len(repo.saved) == 6 and len(pub.events) == 6
    assert report.baseline_cost >= report.total_cost > 0 and report.savings >= 0
    assert report.total_kwh > 0

    (dep,) = report.depots
    assert dep.depot_id == DEPOT.id and dep.vehicles == 6 and dep.capacity_kw == 60
    assert dep.peak_kw_before > 60 and dep.peak_kw_after <= 60 + 1e-6 and dep.feasible and dep.moves > 0

    # aggregate load written to the DB never exceeds the grid capacity in any 15-min slot
    load: dict[datetime, float] = {}
    for rec in repo.saved.values():
        assert rec["status"] == "READY"
        for s in rec["slots"]:
            t = s.start_ts
            while t < s.end_ts:
                load[t] = load.get(t, 0.0) + s.kw
                t = t.replace(minute=t.minute) + (s.end_ts - s.start_ts) / max(
                    1, round((s.end_ts - s.start_ts).total_seconds() / 900)
                )
    assert max(load.values()) <= 60 + 1e-6
    # each vehicle still gets its 50 % (30 kWh) and its persisted cost matches the smoothed slots
    for rec in repo.saved.values():
        assert abs(rec["total_kwh"] - 30.0) < 0.7
        assert abs(rec["total_cost"] - sum(s.cost for s in rec["slots"])) < 0.05


async def test_nightly_counts_failures_and_dry_run_does_not_persist() -> None:
    v = vehicle(1)
    repo = FakeRepo([v], chargers=[])
    planner = PlanVehicle(repo, FakeStore(), FakePublisher(), clock=lambda: NOW)
    report = await NightlyPlanner(repo, planner).run(1, persist=False)
    assert report.failed == 1 and report.planned == 0 and report.failures == {"no chargers configured": 1}
    assert repo.saved == {} and report.depots == []
