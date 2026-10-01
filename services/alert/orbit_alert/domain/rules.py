"""Rule engine: pure functions over (event, context) -> Alert | None. See contracts/api.md "Alert types"."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from orbit_common.dtc import BATTERY_CODES
from orbit_common.events import CanonicalEvent

from orbit_alert.domain.alerts import (
    BATTERY_DTC,
    CHARGE_INTERRUPTED,
    CRITICAL,
    LOW_SOC,
    SOC_DROP,
    SOH_DROP,
    STRAND_RISK,
    WARN,
    Alert,
)

LOW_SOC_PCT = 20.0
STRAND_SOC_PCT = 15.0
SOC_DROP_PTS = 40.0
SOH_DROP_PCT = 80.0
CHARGE_INTERRUPT_SOC_PCT = 80.0
CHARGE_INTERRUPT_GAP_S = 60.0


@dataclass(slots=True)
class RuleContext:
    """Snapshot of everything a rule may need beyond the event itself. Built by the application layer."""

    nominal_range_km: float | None = None
    reachable_charger: bool | None = None  # None = not evaluated (only looked up when soc < STRAND_SOC_PCT)
    max_soc_2h: float | None = None  # highest SoC seen in the trailing 2 h window (excluding this event)
    prev_charging_state: str | None = None
    prev_charging_kw: float = 0.0
    prev_ts_epoch: float | None = None


def remaining_range_km(soc_pct: float | None, nominal_range_km: float | None) -> float | None:
    if soc_pct is None or nominal_range_km is None:
        return None
    return max(0.0, soc_pct / 100.0 * nominal_range_km)


def rule_low_soc(e: CanonicalEvent, _ctx: RuleContext) -> Alert | None:
    if e.soc_pct is not None and e.soc_pct < LOW_SOC_PCT:
        return Alert(e.tenant_id, e.vin, LOW_SOC, WARN, e.ts, {"soc_pct": e.soc_pct})
    return None


def rule_strand_risk(e: CanonicalEvent, ctx: RuleContext) -> Alert | None:
    if e.soc_pct is None or e.soc_pct >= STRAND_SOC_PCT or ctx.reachable_charger is not False:
        return None
    rng = remaining_range_km(e.soc_pct, ctx.nominal_range_km)
    return Alert(
        e.tenant_id,
        e.vin,
        STRAND_RISK,
        CRITICAL,
        e.ts,
        {
            "soc_pct": e.soc_pct,
            "remaining_range_km": None if rng is None else round(rng, 1),
            "nominal_range_km": ctx.nominal_range_km,
            "lat": e.lat,
            "lon": e.lon,
        },
    )


def rule_soc_drop(e: CanonicalEvent, ctx: RuleContext) -> Alert | None:
    if e.soc_pct is None or ctx.max_soc_2h is None:
        return None
    drop = ctx.max_soc_2h - e.soc_pct
    if drop > SOC_DROP_PTS:
        return Alert(
            e.tenant_id,
            e.vin,
            SOC_DROP,
            WARN,
            e.ts,
            {
                "from_soc_pct": ctx.max_soc_2h,
                "to_soc_pct": e.soc_pct,
                "drop_pts": round(drop, 1),
                "window_s": 7200,
            },
        )
    return None


def rule_soh_drop(e: CanonicalEvent, _ctx: RuleContext) -> Alert | None:
    if e.soh_pct is not None and e.soh_pct < SOH_DROP_PCT:
        return Alert(e.tenant_id, e.vin, SOH_DROP, WARN, e.ts, {"soh_pct": e.soh_pct})
    return None


def rule_battery_dtc(e: CanonicalEvent, _ctx: RuleContext) -> Alert | None:
    codes = [c for c in e.dtc if c in BATTERY_CODES]
    if codes:
        return Alert(
            e.tenant_id,
            e.vin,
            BATTERY_DTC,
            CRITICAL,
            e.ts,
            {"codes": codes, "descriptions": {c: BATTERY_CODES[c] for c in codes}},
        )
    return None


def rule_charge_interrupted(e: CanonicalEvent, ctx: RuleContext) -> Alert | None:
    if e.charging_state != "NONE" or ctx.prev_charging_state not in ("AC", "DC"):
        return None
    if e.soc_pct is None or e.soc_pct >= CHARGE_INTERRUPT_SOC_PCT or ctx.prev_charging_kw <= 0:
        return None
    if ctx.prev_ts_epoch is None:
        return None
    gap_s = e.ts.timestamp() - ctx.prev_ts_epoch
    if gap_s < 0 or gap_s > CHARGE_INTERRUPT_GAP_S:  # out-of-order or too long after the last charging sample
        return None
    return Alert(
        e.tenant_id,
        e.vin,
        CHARGE_INTERRUPTED,
        WARN,
        e.ts,
        {
            "soc_pct": e.soc_pct,
            "prev_state": ctx.prev_charging_state,
            "prev_kw": ctx.prev_charging_kw,
            "gap_s": round(gap_s, 1),
        },
    )


Rule = Callable[[CanonicalEvent, RuleContext], Alert | None]

RULES: tuple[Rule, ...] = (
    rule_strand_risk,
    rule_low_soc,
    rule_soc_drop,
    rule_soh_drop,
    rule_battery_dtc,
    rule_charge_interrupted,
)


def evaluate(e: CanonicalEvent, ctx: RuleContext, rules: tuple[Rule, ...] = RULES) -> list[Alert]:
    out: list[Alert] = []
    for rule in rules:
        a = rule(e, ctx)
        if a is not None:
            out.append(a)
    return out
