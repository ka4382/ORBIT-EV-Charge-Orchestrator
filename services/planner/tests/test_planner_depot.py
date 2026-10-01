from __future__ import annotations

import random

import pytest
from orbit_planner.domain.depot import SmoothingResult, VehicleLoad, smooth_depot, total_load

PRICES = [12.5] * 4 + [6.5] * 12  # 16 slots: 4 peak then 12 off-peak


def load(vid: int, offset: int, depart: int, max_kw: float, kw: list[float]) -> VehicleLoad:
    return VehicleLoad(vid, offset, depart, max_kw, kw, list(PRICES))


def energy(v: VehicleLoad) -> float:
    return sum(v.kw)


def test_smoothing_never_exceeds_capacity_when_feasible() -> None:
    # three 22 kW vans all planned in slots 4..7 (cheapest), capacity 44 → one must move
    loads = [load(i, 0, 16, 22, [0.0] * 4 + [22.0] * 4 + [0.0] * 8) for i in range(3)]
    before = [energy(v) for v in loads]
    res = smooth_depot(loads, 44.0, 16)
    assert res.feasible and res.peak_kw_before == 66 and res.peak_kw_after <= 44
    assert all(x <= 44 + 1e-6 for x in res.load_by_slot)
    assert [energy(v) for v in loads] == pytest.approx(before)  # energy (final SoC) preserved per vehicle
    for v in loads:  # nothing outside each vehicle's window
        assert all(v.kw[i] == 0 for i in list(range(0, v.offset)) + list(range(v.depart_idx, 16)))
        assert max(v.kw) <= v.max_kw + 1e-6
    moved_to = [i for i in range(16) if res.load_by_slot[i] > 0 and i not in range(4, 8)]
    assert moved_to and all(PRICES[i] == 6.5 for i in moved_to)  # shifted into other off-peak slots first


def test_vehicle_with_most_slack_moves_first() -> None:
    early = load(1, 0, 6, 22, [0.0] * 4 + [22.0] * 2 + [0.0] * 10)  # departs right after its slots
    late = load(2, 0, 16, 22, [0.0] * 4 + [22.0] * 2 + [0.0] * 10)  # departs much later → has slack
    res = smooth_depot([early, late], 22.0, 16)
    assert res.feasible and res.moves > 0
    assert early.kw[4:6] == [22.0, 22.0]  # untouched
    assert late.kw[4:6] == [0.0, 0.0] and energy(late) == 44


def test_infeasible_when_no_slack_is_flagged() -> None:
    loads = [
        load(i, 0, 4, 22, [22.0] * 4 + [0.0] * 12) for i in range(3)
    ]  # all depart at slot 4, fully packed
    res = smooth_depot(loads, 44.0, 16)
    assert not res.feasible and res.peak_kw_after == 66 and res.moves == 0


def test_empty_and_zero_horizon() -> None:
    assert smooth_depot([], 100, 16) == SmoothingResult([], 100, 0.0, 0.0, 0, True, [])
    assert smooth_depot([load(1, 0, 4, 22, [22.0] * 4)], 10, 0).feasible
    assert total_load([load(1, 0, 4, 22, [22.0] * 4 + [0.0] * 12)], 4) == [22.0] * 4


def test_randomised_fleet_respects_capacity_and_conserves_energy() -> None:
    rng = random.Random(7)
    horizon = 40
    loads = []
    for i in range(60):
        offset, depart = rng.randrange(0, 10), rng.randrange(30, 41)
        need = rng.randrange(2, 8)
        kw = [0.0] * horizon
        for t in rng.sample(range(offset, depart), need):
            kw[t] = 22.0
        loads.append(
            VehicleLoad(i, offset, depart, 22.0, kw, [rng.choice([6.5, 9.0, 12.5]) for _ in range(horizon)])
        )
    before = [energy(v) for v in loads]
    res = smooth_depot(loads, 200.0, horizon)
    assert res.feasible and max(res.load_by_slot) <= 200 + 1e-6
    assert [energy(v) for v in loads] == pytest.approx(before)
    assert all(max(v.kw) <= 22 + 1e-6 for v in loads)
