"""Depot grid-capacity smoothing: a greedy post-pass over many per-vehicle DP schedules.

Problem  Each vehicle's schedule is individually cost-optimal, but at one depot the sum of kW in a 15-min
         slot may exceed `grid_capacity_kw`. The exact problem (multi-vehicle scheduling with a shared
         capacity, per-vehicle windows and time-varying prices) is NP-hard (generalises bin packing /
         parallel-machine scheduling with release dates and deadlines), so we use a heuristic.

Heuristic  For each slot t whose load exceeds capacity, take the vehicles charging in t ordered by *slack*
         (latest departure first, then the most spare slots), and move their kW at t to their next-cheapest
         feasible slot t' — one inside the vehicle's [arrival, departure) window where both the vehicle's own
         max kW and the depot's remaining capacity have room. Repeat passes until every slot is within
         capacity or a full pass makes no move. Per-vehicle energy (hence final SoC) is preserved, so SoC
         never exceeds 100 % and departure targets are unaffected.

Time  O(passes × T × V × T log T) worst case (sorting a vehicle's window per move); in practice only
      overloaded slots and the few vehicles with slack are touched. Space O(V × T).
"""

from __future__ import annotations

from dataclasses import dataclass, field

_EPS = 1e-6


@dataclass(slots=True)
class VehicleLoad:
    """A vehicle's charging profile on the depot's common slot horizon."""

    vehicle_id: int
    offset: int  # first slot index the vehicle can charge in (its arrival)
    depart_idx: int  # exclusive end of its window
    max_kw: float
    kw: list[float]  # per horizon slot; non-zero only in [offset, depart_idx)
    prices: list[float]  # ₹/kWh per horizon slot as seen by this vehicle

    @property
    def energy_kwh_per_slot_h(self) -> float:
        return sum(self.kw)

    def free_slots(self) -> int:
        return sum(1 for i in range(self.offset, self.depart_idx) if self.kw[i] < self.max_kw - _EPS)


@dataclass(slots=True)
class SmoothingResult:
    loads: list[VehicleLoad]
    capacity_kw: float
    peak_kw_before: float
    peak_kw_after: float
    moves: int
    feasible: bool
    load_by_slot: list[float] = field(default_factory=list)


def total_load(loads: list[VehicleLoad], horizon: int) -> list[float]:
    out = [0.0] * horizon
    for v in loads:
        for i, kw in enumerate(v.kw[:horizon]):
            out[i] += kw
    return out


def smooth_depot(
    loads: list[VehicleLoad], capacity_kw: float, horizon: int, max_passes: int = 25
) -> SmoothingResult:
    """Shift charge between slots (in place) until Σ kW ≤ capacity in every slot, or no move helps."""
    if horizon <= 0 or not loads:
        return SmoothingResult(loads, capacity_kw, 0.0, 0.0, 0, True, [])
    load = total_load(loads, horizon)
    peak_before = max(load)
    moves = 0
    for _ in range(max_passes):
        moved_any = False
        for t in range(horizon):
            excess = load[t] - capacity_kw
            if excess <= _EPS:
                continue
            # most slack first: latest departure, then most spare slots in its window
            candidates = sorted(
                (v for v in loads if v.kw[t] > _EPS), key=lambda v: (-v.depart_idx, -v.free_slots())
            )
            for v in candidates:
                if excess <= _EPS:
                    break
                amount = min(v.kw[t], excess)
                window = sorted(
                    (i for i in range(v.offset, min(v.depart_idx, horizon)) if i != t),
                    key=lambda i: (v.prices[i], abs(i - t)),
                )  # next-cheapest, nearest on ties
                for t2 in window:
                    room = min(v.max_kw - v.kw[t2], capacity_kw - load[t2])
                    if room <= _EPS:
                        continue
                    q = min(amount, room)
                    v.kw[t2] += q
                    v.kw[t] -= q
                    load[t2] += q
                    load[t] -= q
                    amount -= q
                    excess -= q
                    moves += 1
                    moved_any = True
                    if amount <= _EPS:
                        break
        if max(load) <= capacity_kw + _EPS or not moved_any:
            break
    peak_after = max(load)
    return SmoothingResult(
        loads, capacity_kw, peak_before, peak_after, moves, peak_after <= capacity_kw + _EPS, load
    )
