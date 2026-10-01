from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from orbit_planner.domain import schedule as sc
from planner_fakes import SEED_TARIFF, ist

# Wednesday 1 Oct 2026, 17:00 IST arrival, 06:00 next day departure → 52 slots of 15 min
ARRIVE, DEPART = ist(2026, 10, 1, 17, 0), ist(2026, 10, 2, 6, 0)


def req(**kw: Any) -> sc.ScheduleRequest:
    base: dict[str, Any] = dict(
        soc_now=30,
        target_soc=80,
        battery_kwh=60,
        max_kw=22,
        arrival_ts=ARRIVE,
        depart_ts=DEPART,
        tariff=SEED_TARIFF,
    )
    base.update(kw)
    return sc.ScheduleRequest(**base)


def local_hour(ts) -> float:
    lt = ts.astimezone(sc.IST)
    return lt.hour + lt.minute / 60


def test_slot_prices_follow_local_tariff_windows() -> None:
    prices = sc.slot_prices(ARRIVE, 52, 15, SEED_TARIFF)
    assert prices[:4] == [9.0] * 4  # 17:00–18:00 normal
    assert prices[4:20] == [12.5] * 16  # 18:00–22:00 peak
    assert prices[20:] == [6.5] * 32  # 22:00–06:00 off-peak
    assert sc.slot_prices(ARRIVE, 2, 15, [], default=7.7) == [7.7, 7.7]
    assert {s.price_per_kwh for s in sc.flat_tariff(16.0)} == {16.0} and len(sc.flat_tariff(1)) == 7


def test_dp_prefers_off_peak_and_beats_baseline() -> None:
    s = sc.optimise(req())
    assert s.feasible and s.final_soc >= 80
    assert s.total_kwh == pytest.approx(30.0, abs=0.6)  # 50 % of 60 kWh
    for slot in s.slots:  # everything lands in 22:00–06:00
        assert slot.price_per_kwh == 6.5
        assert local_hour(slot.start_ts) >= 22 or local_hour(slot.start_ts) < 6
    assert s.total_cost == pytest.approx(30.0 * 6.5, abs=4.0)
    assert s.baseline_cost > s.total_cost  # naive policy pays normal + peak prices
    # naive: 6 slots of 9 % (5.4 kWh) → overshoots to 84 %: 4 slots @ ₹9 then 2 at peak ₹12.5
    assert s.baseline_cost == pytest.approx(4 * 5.4 * 9.0 + 2 * 5.4 * 12.5, abs=0.05)
    assert s.savings == pytest.approx(s.baseline_cost - s.total_cost)
    assert 0 < sc.savings_pct(s) < 100
    assert s.total_kwh == pytest.approx(sum(sl.kwh for sl in s.slots), abs=1e-6)
    assert s.n_slots == 52 and sum(1 for k in s.kw_by_slot if k > 0) == len(s.slots) or len(s.slots) < 52


def test_slots_are_merged_contiguous_runs_and_energy_consistent() -> None:
    s = sc.optimise(req())
    for a, b in zip(s.slots, s.slots[1:], strict=False):
        assert b.start_ts >= a.end_ts
        if b.start_ts == a.end_ts:  # adjacent runs differ in kW or price
            assert abs(a.kw - b.kw) > 1e-6 or a.price_per_kwh != b.price_per_kwh
    for slot in s.slots:
        hours = (slot.end_ts - slot.start_ts).total_seconds() / 3600
        assert slot.kwh == pytest.approx(slot.kw * hours, abs=1e-2)
        assert slot.cost == pytest.approx(slot.kwh * slot.price_per_kwh, abs=0.02)
        assert slot.kw <= 22 + 1e-6


def test_never_exceeds_100_soc_and_reaches_target() -> None:
    s = sc.optimise(req(soc_now=95, target_soc=100, max_kw=60))  # one slot at 60 kW = 25 % → clamps at 100
    assert s.feasible and s.final_soc == 100
    assert s.total_kwh == pytest.approx(3.0, abs=1e-6)  # exactly the 5 % that fits
    assert s.slots[0].kw < 60  # derived from realised energy
    assert max(sc.optimise(req(soc_now=10, target_soc=100, max_kw=150)).kw_by_slot) > 0
    for target in (50, 80, 100):
        out = sc.optimise(req(target_soc=target))
        assert out.feasible and target <= out.final_soc <= 100


def test_baseline_cost_is_never_below_optimal(tariff) -> None:
    for soc, tgt, kw, arrive_h in [(30, 80, 22, 17), (10, 100, 7.4, 8), (50, 90, 60, 23), (0, 100, 150, 19)]:
        arrive = ist(2026, 10, 1, arrive_h)
        s = sc.optimise(
            req(
                soc_now=soc,
                target_soc=tgt,
                max_kw=kw,
                arrival_ts=arrive,
                depart_ts=arrive + timedelta(hours=10),
            )
        )
        assert s.baseline_cost + 1e-6 >= s.total_cost
        assert s.baseline_objective + 1e-6 >= s.objective


def test_infeasible_target_is_flagged_and_reaches_max_soc() -> None:
    s = sc.optimise(req(soc_now=10, target_soc=90, max_kw=3.6, depart_ts=ARRIVE + timedelta(hours=2)))
    assert not s.feasible
    assert 10 < s.final_soc < 90
    assert sum(1 for k in s.kw_by_slot if k > 0) == 8  # charges every one of the 8 slots
    assert s.baseline_cost == pytest.approx(s.total_cost, abs=0.02)  # only one policy reaches the max SoC
    zero = sc.optimise(req(depart_ts=ARRIVE))  # departs on arrival: nothing possible
    assert not zero.feasible and zero.final_soc == 30 and zero.slots == [] and zero.baseline_cost == 0
    already = sc.optimise(req(soc_now=85, depart_ts=ARRIVE))
    assert already.feasible and already.slots == []


def test_degradation_penalties_change_the_objective_not_the_bill() -> None:
    plain = sc.optimise(req(target_soc=100))
    pen = sc.optimise(req(target_soc=100, lambda_above_80=3.0, lambda_dc=2.0, is_dc=True))
    assert pen.objective > plain.objective
    assert pen.objective == pytest.approx(pen.total_cost + 3.0 * 0.2 * 60 + 2.0 * pen.total_kwh, abs=0.6)
    assert pen.final_soc == 100 and pen.feasible
    no_dc = sc.optimise(req(target_soc=100, lambda_dc=2.0, is_dc=False))
    assert no_dc.objective == pytest.approx(plain.objective, abs=1e-6)


def test_with_kw_rebuilds_slots_from_vector() -> None:
    s = sc.optimise(req())
    moved = list(s.kw_by_slot)
    moved[0], moved[-1] = moved[-1], moved[0]
    s2 = s.with_kw(moved)
    assert s2.total_kwh == pytest.approx(s.total_kwh, abs=1e-6)
    assert s2.baseline_cost == s.baseline_cost and s2.kw_by_slot == moved
    assert s2.total_cost != s.total_cost or moved == s.kw_by_slot


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        sc.optimise(req(battery_kwh=0))
    with pytest.raises(ValueError):
        sc.optimise(req(slot_minutes=0))
