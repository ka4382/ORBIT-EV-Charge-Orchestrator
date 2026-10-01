"""Cost-optimal charging schedule under a time-of-use tariff, by dynamic programming.

State      (t, s): after `t` slots of `slot_minutes`, battery at `s` % (integer buckets 0..100).
Transition charge at power level p ∈ {0, 50 %, 100 %} of `max_kw` for one slot → s' = min(100, s + Δp).
Cost       price[t] × kWh + λ80 × kWh charged above 80 % + λdc × kWh (if DC fast charging).
Goal       min cost with s_T ≥ target; if unreachable, the plan that reaches the highest SoC at least cost.

Time  O(T × S × P) — S = 101 buckets, P power levels; the inner loop over S is vectorised with numpy.
Space O(T × S) for the back-pointers (two int arrays), O(S) for the rolling cost vector.

`baseline_cost` is the cost of the naive policy "plug in on arrival, charge at max power until target",
which is itself one of the DP's policies, so `baseline_cost ≥ total_cost` always holds (for λ = 0 exactly,
for λ > 0 on the `objective` that includes the penalties).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

import numpy as np

IST = ZoneInfo("Asia/Kolkata")
SOC_BUCKETS = 101
_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class TariffSlot:
    """One row of `tariff_slot`: local day-of-week (0 = Monday), minutes since local midnight, ₹/kWh."""

    dow: int
    start_min: int
    end_min: int
    price_per_kwh: float


@dataclass(frozen=True, slots=True)
class Slot:
    """One contiguous charging run (maps 1:1 to a `charging_slot` row)."""

    start_ts: datetime
    end_ts: datetime
    kw: float
    kwh: float
    cost: float
    price_per_kwh: float


@dataclass(frozen=True, slots=True)
class ScheduleRequest:
    soc_now: float
    target_soc: float
    battery_kwh: float
    max_kw: float
    arrival_ts: datetime
    depart_ts: datetime
    tariff: Sequence[TariffSlot]
    tz: tzinfo = IST
    slot_minutes: int = 15
    lambda_above_80: float = 0.0  # ₹/kWh shadow price for charging above 80 % (calendar ageing)
    lambda_dc: float = 0.0  # ₹/kWh shadow price for DC fast charging (cycle ageing)
    is_dc: bool = False
    power_levels: tuple[float, ...] = (0.0, 0.5, 1.0)
    default_price: float = 9.0  # used when no tariff slot covers a time


@dataclass(slots=True)
class Schedule:
    slots: list[Slot]
    total_cost: float  # Σ slot tariff cost (money)
    total_kwh: float
    baseline_cost: float  # money spent by the naive "charge on arrival" policy
    final_soc: int
    feasible: bool  # False → target not reachable before departure; plan reaches final_soc
    objective: float  # DP objective = total_cost + degradation penalties
    baseline_objective: float
    arrival_ts: datetime
    slot_minutes: int
    kw_by_slot: list[float] = field(default_factory=list)  # raw per-slot kW (input to depot smoothing)
    prices: list[float] = field(default_factory=list)  # ₹/kWh per slot

    @property
    def savings(self) -> float:
        return round(self.baseline_cost - self.total_cost, 2)

    @property
    def n_slots(self) -> int:
        return len(self.kw_by_slot)

    def with_kw(self, kw_by_slot: Sequence[float]) -> Schedule:
        """Rebuild slots/costs from a modified per-slot kW vector (after depot smoothing)."""
        kw = [float(x) for x in kw_by_slot]
        slots = slots_from_kw(kw, self.prices, self.arrival_ts, self.slot_minutes)
        return Schedule(
            slots=slots,
            total_cost=round(sum(s.cost for s in slots), 2),
            total_kwh=round(sum(s.kwh for s in slots), 2),
            baseline_cost=self.baseline_cost,
            final_soc=self.final_soc,
            feasible=self.feasible,
            objective=self.objective,
            baseline_objective=self.baseline_objective,
            arrival_ts=self.arrival_ts,
            slot_minutes=self.slot_minutes,
            kw_by_slot=kw,
            prices=list(self.prices),
        )


def flat_tariff(price_per_kwh: float) -> tuple[TariffSlot, ...]:
    """A constant price every day (public chargers with a fixed ₹/kWh)."""
    return tuple(TariffSlot(d, 0, 1440, price_per_kwh) for d in range(7))


def slot_prices(
    start: datetime,
    n_slots: int,
    slot_minutes: int,
    tariff: Sequence[TariffSlot],
    tz: tzinfo = IST,
    default: float = 9.0,
) -> list[float]:
    """₹/kWh for each of `n_slots` slots from `start`, evaluated in the tariff's local time. O(T × k)."""
    by_dow: dict[int, list[TariffSlot]] = {}
    for ts in tariff:
        by_dow.setdefault(ts.dow, []).append(ts)
    out: list[float] = []
    for i in range(n_slots):
        local = (start + timedelta(minutes=i * slot_minutes)).astimezone(tz)
        minute = local.hour * 60 + local.minute
        price = default
        for ts in by_dow.get(local.weekday(), ()):
            if ts.start_min <= minute < ts.end_min:
                price = ts.price_per_kwh
                break
        out.append(price)
    return out


def slots_from_kw(
    kw_by_slot: Sequence[float], prices: Sequence[float], arrival_ts: datetime, slot_minutes: int
) -> list[Slot]:
    """Turn a per-slot kW vector into merged contiguous `Slot` runs (same kW and same price). O(T)."""
    slot_h = slot_minutes / 60.0
    out: list[Slot] = []
    for i, kw in enumerate(kw_by_slot):
        if kw <= _EPS:
            continue
        start = arrival_ts + timedelta(minutes=i * slot_minutes)
        end = start + timedelta(minutes=slot_minutes)
        kwh = kw * slot_h
        cost = prices[i] * kwh
        last = out[-1] if out else None
        if last and last.end_ts == start and abs(last.kw - kw) < 1e-6 and last.price_per_kwh == prices[i]:
            out[-1] = Slot(last.start_ts, end, kw, last.kwh + kwh, last.cost + cost, prices[i])
        else:
            out.append(Slot(start, end, kw, kwh, cost, prices[i]))
    return [
        Slot(s.start_ts, s.end_ts, round(s.kw, 3), round(s.kwh, 3), round(s.cost, 2), s.price_per_kwh)
        for s in out
    ]


def _bucket(soc: float) -> int:
    return int(min(100, max(0, round(soc))))


def optimise(req: ScheduleRequest) -> Schedule:
    """Run the DP and return the minimal-cost schedule (see module docstring). O(T × S × P) time."""
    if req.battery_kwh <= 0 or req.max_kw < 0 or req.slot_minutes <= 0:
        raise ValueError("battery_kwh, max_kw and slot_minutes must be positive")
    slot_h = req.slot_minutes / 60.0
    horizon_min = (req.depart_ts - req.arrival_ts).total_seconds() / 60.0
    n_slots = max(0, int(horizon_min // req.slot_minutes))
    prices = slot_prices(req.arrival_ts, n_slots, req.slot_minutes, req.tariff, req.tz, req.default_price)
    s0, target = _bucket(req.soc_now), _bucket(req.target_soc)
    battery = req.battery_kwh
    lam80, lamdc = req.lambda_above_80, (req.lambda_dc if req.is_dc else 0.0)

    # bucket delta per power level; levels that round to 0 % are equivalent to idling and skipped
    levels = [(p, int(round(req.max_kw * p * slot_h / battery * 100))) for p in req.power_levels]
    charge_levels = [(pi, d) for pi, (p, d) in enumerate(levels) if p > 0 and d > 0]

    s_idx = np.arange(SOC_BUCKETS)
    cost = np.full(SOC_BUCKETS, np.inf)
    cost[s0] = 0.0
    back_prev = np.zeros((n_slots, SOC_BUCKETS), dtype=np.int16)
    back_level = np.zeros((n_slots, SOC_BUCKETS), dtype=np.int8)

    def transition_cost(base: np.ndarray, from_s: np.ndarray, to_s: np.ndarray, price: float) -> np.ndarray:
        """Vectorised cost of moving from buckets `from_s` to `to_s` in one slot at tariff `price`."""
        kwh = (to_s - from_s) / 100.0 * battery
        above = np.maximum(0, to_s - np.maximum(from_s, 80)) / 100.0 * battery
        return base + (price + lamdc) * kwh + lam80 * above

    for t in range(n_slots):
        new = cost.copy()  # p = 0: idle, free
        prev = s_idx.astype(np.int16)
        lvl = np.zeros(SOC_BUCKETS, dtype=np.int8)
        for pi, d in charge_levels:
            m = SOC_BUCKETS - d  # states that do not clamp at 100
            tgt = s_idx[:m] + d
            cand = transition_cost(cost[:m], s_idx[:m], tgt, prices[t])
            better = cand < new[tgt]
            new[tgt[better]] = cand[better]
            prev[tgt[better]] = s_idx[:m][better]
            lvl[tgt[better]] = pi
            if m < SOC_BUCKETS:  # clamped states all land on 100: keep the cheapest
                clamped = transition_cost(cost[m:], s_idx[m:], np.full(SOC_BUCKETS - m, 100), prices[t])
                j = int(np.argmin(clamped))
                if clamped[j] < new[100]:
                    new[100] = clamped[j]
                    prev[100] = m + j
                    lvl[100] = pi
        cost = new
        back_prev[t] = prev
        back_level[t] = lvl

    finite = np.isfinite(cost)
    feasible = bool(finite[target:].any())
    # feasible → cheapest final SoC ≥ target; else the highest reachable SoC (only one policy reaches it)
    best = target + int(np.argmin(cost[target:])) if feasible else int(np.max(np.nonzero(finite)[0]))

    # backtrack → per-slot kW derived from the realised bucket delta (energy-consistent with SoC)
    kw_by_slot = [0.0] * n_slots
    s = best
    for t in range(n_slots - 1, -1, -1):
        ps = int(back_prev[t][s])
        if ps != s:
            kw_by_slot[t] = (s - ps) / 100.0 * battery / slot_h
        s = ps
    slots = slots_from_kw(kw_by_slot, prices, req.arrival_ts, req.slot_minutes)
    total_cost = round(sum(sl.cost for sl in slots), 2)
    total_kwh = round(sum(sl.kwh for sl in slots), 2)

    base_cost, base_obj = _baseline(
        s0, target, levels[-1][1] if levels else 0, n_slots, prices, battery, lam80, lamdc
    )
    return Schedule(
        slots=slots,
        total_cost=total_cost,
        total_kwh=total_kwh,
        baseline_cost=round(base_cost, 2),
        final_soc=best,
        feasible=feasible,
        objective=float(cost[best]),
        baseline_objective=base_obj,
        arrival_ts=req.arrival_ts,
        slot_minutes=req.slot_minutes,
        kw_by_slot=kw_by_slot,
        prices=prices,
    )


def _baseline(
    s0: int,
    target: int,
    d_max: int,
    n_slots: int,
    prices: Sequence[float],
    battery: float,
    lam80: float,
    lamdc: float,
) -> tuple[float, float]:
    """Naive policy: from arrival, charge at max power every slot until SoC ≥ target. O(T)."""
    s, money, obj = s0, 0.0, 0.0
    for t in range(n_slots):
        if s >= target or d_max <= 0:
            break
        s2 = min(100, s + d_max)
        kwh = (s2 - s) / 100.0 * battery
        above = max(0, s2 - max(s, 80)) / 100.0 * battery
        money += prices[t] * kwh
        obj += (prices[t] + lamdc) * kwh + lam80 * above
        s = s2
    return money, obj


def savings_pct(schedule: Schedule) -> float:
    if schedule.baseline_cost <= 0:
        return 0.0
    return round(100.0 * (schedule.baseline_cost - schedule.total_cost) / schedule.baseline_cost, 1)


__all__ = [
    "IST",
    "SOC_BUCKETS",
    "Schedule",
    "ScheduleRequest",
    "Slot",
    "TariffSlot",
    "flat_tariff",
    "optimise",
    "savings_pct",
    "slot_prices",
    "slots_from_kw",
]
