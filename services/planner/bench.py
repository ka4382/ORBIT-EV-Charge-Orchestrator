"""Planner micro-benchmarks (numbers go into docs/algorithms.md "Scale tested").

    cd services && uv run python planner/bench.py [--vehicles 10000] [--repeat 200]

No external services: the 10K-vehicle run drives the real use case (PlanVehicle.compute — Dijkstra + DP)
against in-memory fakes, so it measures the algorithms and orchestration, not Postgres/Kafka round-trips.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from orbit_planner.application.plan_vehicle import PlannerConfig, PlanVehicle
from orbit_planner.domain import graph as g
from orbit_planner.domain import schedule as sc
from orbit_planner.domain.depot import VehicleLoad, smooth_depot
from orbit_planner.domain.models import Charger, Depot, PlanRequest, Vehicle, VehicleModel, VehicleState
from orbit_planner.domain.schedule import Slot
from orbit_planner.domain.trips import GpsPoint, segment

CITY = (13.0827, 80.2707)  # Chennai
TARIFF = tuple(
    sc.TariffSlot(dow, s, e, p)
    for dow in range(7)
    for s, e, p in ((0, 360, 6.5), (360, 1080, 9.0), (1080, 1320, 12.5), (1320, 1440, 6.5))
)
VAN = VehicleModel(1, "A-Van 60", "BEV", 60.0, 320.0, 11.0, 100.0)


def ms(samples: list[float]) -> str:
    p95 = sorted(samples)[max(0, int(len(samples) * 0.95) - 1)] * 1e3
    return f"median {statistics.median(samples) * 1e3:.2f} ms, p95 {p95:.2f} ms"


def bench_dijkstra(repeat: int) -> tuple[str, str]:
    rng = random.Random(1)
    t0 = time.perf_counter()
    gr = g.build_graph(*CITY)
    build = time.perf_counter() - t0
    chargers = [
        Charger(i, f"P{i}", CITY[0] + rng.uniform(-0.25, 0.25), CITY[1] + rng.uniform(-0.25, 0.25), 60.0)
        for i in range(40)
    ]
    samples: list[float] = []
    for _ in range(repeat):
        origin = (CITY[0] + rng.uniform(-0.25, 0.25), CITY[1] + rng.uniform(-0.25, 0.25))
        t0 = time.perf_counter()
        g.nearest_reachable(gr, origin, chargers, range_km=60.0, k=5)
        samples.append(time.perf_counter() - t0)
    full: list[float] = []
    for _ in range(max(1, repeat // 10)):
        origin = (CITY[0] + rng.uniform(-0.25, 0.25), CITY[1] + rng.uniform(-0.25, 0.25))
        t0 = time.perf_counter()
        g.reachable_set(gr, origin, 1e9)  # whole graph, no early exit
        full.append(time.perf_counter() - t0)
    return (
        f"build {gr.node_count} nodes / {gr.edge_count} edges: {build * 1e3:.1f} ms; "
        f"nearest_reachable(40 chargers, range 60 km, k=5): {ms(samples)}",
        f"full Dijkstra (no cut-off, {gr.node_count} nodes): {ms(full)}",
    )


def bench_dp(repeat: int) -> str:
    arrive = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)  # 18:00 IST
    req = sc.ScheduleRequest(30, 90, 60, 22, arrive, arrive + timedelta(hours=24), TARIFF)  # 96 slots
    samples: list[float] = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        s = sc.optimise(req)
        samples.append(time.perf_counter() - t0)
    assert s.n_slots == 96 and s.feasible
    return (
        f"96 slots × 101 SoC × 3 powers: {ms(samples)} (savings {sc.savings_pct(s)} % vs charge-on-arrival)"
    )


def bench_depot(vehicles: int) -> str:
    rng = random.Random(3)
    horizon = 40
    loads = []
    for i in range(vehicles):
        kw = [0.0] * horizon
        for t in rng.sample(range(0, 32), 6):
            kw[t] = 22.0
        loads.append(
            VehicleLoad(i, 0, horizon, 22.0, kw, [rng.choice([6.5, 9.0, 12.5]) for _ in range(horizon)])
        )
    t0 = time.perf_counter()
    res = smooth_depot(loads, capacity_kw=vehicles * 22 * 0.2, horizon=horizon)
    dt = time.perf_counter() - t0
    return (
        f"{vehicles} vehicles × {horizon} slots: {dt * 1e3:.1f} ms, peak {res.peak_kw_before:.0f} → "
        f"{res.peak_kw_after:.0f} kW (cap {res.capacity_kw:.0f}), {res.moves} moves, feasible={res.feasible}"
    )


def bench_trips(n: int) -> str:
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    rng = random.Random(5)
    pts, lat, lon = [], 13.0, 80.0
    for i in range(n):
        moving = (i // 90) % 2 == 0
        lat += 0.0005 if moving else rng.uniform(-0.00003, 0.00003)
        pts.append(
            GpsPoint(t0 + timedelta(seconds=10 * i), lat, lon, 40.0 if moving else 0.5, 80 - i * 0.001)
        )
    t1 = time.perf_counter()
    seg = segment(pts, 60.0)
    dt = (time.perf_counter() - t1) * 1e3
    return f"{n} samples: {dt:.1f} ms → {len(seg.trips)} trips, {len(seg.stops)} stops"


class _Repo:
    """In-memory PlanRepository (only the read side is exercised by `compute`)."""

    def __init__(self, vehicles: dict[str, Vehicle], chargers: list[Charger]) -> None:
        self.vehicles, self.chargers = vehicles, chargers

    async def get_vehicle_by_vin(self, vin: str) -> Vehicle | None:
        return self.vehicles.get(vin)

    async def list_bevs(self, tenant_id: int) -> list[Vehicle]:
        return list(self.vehicles.values())

    async def list_tenants(self) -> list[int]:
        return [1]

    async def depot_chargers(self, depot_id: int) -> list[Charger]:
        return self.chargers

    async def create_plan(
        self, vehicle_id: int, target_soc: float, depart_ts: datetime, requested_by: str
    ) -> int:
        return vehicle_id

    async def save_plan(
        self,
        plan_id: int,
        status: str,
        charger_id: int | None,
        slots: Sequence[Slot],
        total_cost: float | None,
        total_kwh: float | None,
        baseline_cost: float | None,
    ) -> None:
        return None


class _Store:
    def __init__(self, states: dict[str, VehicleState], public: list[Charger]) -> None:
        self.states, self.public = states, public

    async def vehicle_state(self, vin: str) -> VehicleState | None:
        return self.states.get(vin)

    async def public_chargers_near(self, lat: float, lon: float, radius_km: float) -> list[Charger]:
        return self.public


class _Publisher:
    async def plan_ready(self, tenant_id: int, vehicle_id: int | None, event: dict[str, object]) -> None:
        return None


async def bench_vehicles(n: int) -> str:
    rng = random.Random(11)
    depot = Depot(1, CITY[0] + 0.05, CITY[1] - 0.03, 900.0, TARIFF)
    chargers = [
        Charger(
            i,
            f"D1-C{i}",
            depot.lat + rng.uniform(-0.001, 0.001),
            depot.lon + rng.uniform(-0.001, 0.001),
            22.0 if i < 8 else 60.0,
            "Type2" if i < 8 else "CCS2",
            False,
            None,
            1,
        )
        for i in range(12)
    ]
    public = [
        Charger(
            100 + i,
            f"P{i}",
            CITY[0] + rng.uniform(-0.25, 0.25),
            CITY[1] + rng.uniform(-0.25, 0.25),
            rng.choice([50.0, 120.0]),
            "CCS2",
            True,
            16.0,
            None,
        )
        for i in range(40)
    ]
    vehicles, states = {}, {}
    for i in range(n):
        v = Vehicle(i, f"VIN{i:014d}", 1, 1, VAN, depot)
        vehicles[v.vin] = v
        states[v.vin] = VehicleState(
            rng.uniform(5, 85), CITY[0] + rng.uniform(-0.2, 0.2), CITY[1] + rng.uniform(-0.2, 0.2)
        )
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    depart = now + timedelta(hours=12)
    planner = PlanVehicle(
        _Repo(vehicles, chargers), _Store(states, public), _Publisher(), PlannerConfig(), clock=lambda: now
    )
    planner.graph_factory(depot.lat, depot.lon)  # warm the graph cache (built once per city)
    t0 = time.perf_counter()
    status: dict[str, int] = {}
    for vin in vehicles:
        out = await planner.compute(PlanRequest(1, vin, 1, 90.0, depart))
        status[out.status] = status.get(out.status, 0) + 1
    dt = time.perf_counter() - t0
    per = dt / n * 1e3
    return (
        f"{n} vehicles planned sequentially (Dijkstra + DP each): {dt:.1f} s ({per:.2f} ms/vehicle), {status}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vehicles", type=int, default=10_000)
    ap.add_argument("--repeat", type=int, default=200)
    args = ap.parse_args()
    d1, d2 = bench_dijkstra(args.repeat)
    print("Dijkstra 60×60:", d1)
    print("Dijkstra 60×60:", d2)
    print("DP schedule   :", bench_dp(args.repeat))
    print("Depot smooth  :", bench_depot(300))
    print("Trip segment  :", bench_trips(100_000))
    print("Plan vehicles :", asyncio.run(bench_vehicles(args.vehicles)))


if __name__ == "__main__":
    main()
