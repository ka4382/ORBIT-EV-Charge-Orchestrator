"""The agent's tool allowlist. Each tool is a typed async function over the tenant-scoped repositories with
a pydantic argument model (JSON schema sent to the model, validation on the way back).

Guardrails enforced here, not by the prompt:
- tenant and role come from the verified JWT (`ToolContext.principal`, `repos` already RLS-scoped); no tool
  has a tenant/role parameter and unknown arguments are rejected (`extra="forbid"`);
- per-role allowlist, max tool calls per query, VIN check-digit validation;
- `propose_replan` only inserts PENDING `agent_action` rows after an SQL ownership check of every VIN.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from orbit_common.vin import is_valid
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..auth import Principal
from ..masking import mask_location
from ..repositories.base import Repos, Row
from ..repositories.postgres import PEAK_PRICE_PER_KWH
from ..state import StateStore
from .guard import numbers_in, to_jsonable, wrap_error, wrap_result

DEFAULT_TARGET_SOC = 80.0
AT_RISK_SOC = 30.0
CRITICAL_SOC = 15.0
DEFAULT_RANGE_KM = 50.0
REACHABLE_FACTOR = 0.9
SCAN_PAGE = 500
SCAN_MAX = 5000
MAX_REPLAN_VINS = 50
BUDGET_ERROR = "tool_call_cap"
BUDGET_MESSAGE = "tool call limit reached for this question; answer with the results you already have"


class ToolError(Exception):
    """A tool refused or failed; the message is returned to the model as an error result."""


def _vin(value: str) -> str:
    v = str(value).strip().upper()
    if not is_valid(v):
        raise ValueError(f"'{value}' is not a valid VIN (format or check digit)")
    return v


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class LimitArgs(_Args):
    limit: int = Field(default=10, ge=1, le=50, description="Maximum number of vehicles to return")


class VinArgs(_Args):
    vin: str = Field(description="17-character VIN of a vehicle in the caller's fleet")

    @field_validator("vin")
    @classmethod
    def _valid_vin(cls, v: str) -> str:
        return _vin(v)


class NearestArgs(VinArgs):
    limit: int = Field(default=3, ge=1, le=10, description="How many chargers to return")


class PlanArgs(_Args):
    plan_id: int = Field(ge=1, description="Charging plan id")


class CostArgs(_Args):
    days: int = Field(default=7, ge=1, le=90, description="Look-back window in days ending now")


class AlertArgs(_Args):
    severity: Literal["INFO", "WARN", "CRITICAL"] | None = Field(
        default=None, description="Filter by severity; omit for all"
    )
    limit: int = Field(default=10, ge=1, le=50)


class ReplanArgs(_Args):
    vins: list[str] = Field(min_length=1, max_length=MAX_REPLAN_VINS, description="VINs to re-plan (max 50)")
    target_soc: float = Field(ge=20, le=100, description="Target state of charge in percent")
    depart_ts: datetime = Field(description="ISO-8601 departure time; must be in the next 7 days")
    rationale: str = Field(min_length=1, max_length=500, description="Why this re-plan is proposed")

    @field_validator("vins")
    @classmethod
    def _vins(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(_vin(x) for x in v))


@dataclass
class ToolContext:
    repos: Repos
    store: StateStore | None
    principal: Principal
    run_id: str
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


Handler = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: type[_Args]
    handler: Handler
    write: bool = False

    def schema(self) -> dict[str, Any]:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        schema["additionalProperties"] = False
        schema.setdefault("properties", {})
        return {"name": self.name, "description": self.description, "input_schema": schema}


# --------------------------------------------------------------------------------------------- handlers


async def get_fleet_summary(ctx: ToolContext, _: NoArgs) -> dict[str, Any]:
    out: dict[str, Any] = {
        "vehicles": 0,
        "online": 0,
        "charging": 0,
        "at_risk": 0,
        "avg_soc": 0.0,
        "kwh_today": 0.0,
    }
    live = await ctx.store.summary(ctx.repos.tenant_id) if ctx.store is not None else None
    if live:
        out.update({k: live.get(k, out[k]) for k in out})
    else:
        out["vehicles"] = await ctx.repos.vehicles.count()
    cost, savings = await ctx.repos.plans.totals_today()
    out.update(
        open_alerts=await ctx.repos.alerts.count_open(),
        cost_today=round(cost, 2),
        savings_today=round(savings, 2),
        live_state_available=bool(live),
    )
    return out


async def _tariff_prices(ctx: ToolContext) -> tuple[float | None, float]:
    t = await ctx.repos.tariffs.current()
    prices = [float(s["price_per_kwh"]) for s in (t or {}).get("slots", []) if s.get("price_per_kwh")]
    if not prices:
        return None, PEAK_PRICE_PER_KWH
    return min(prices), max(max(prices), PEAK_PRICE_PER_KWH)


async def list_at_risk_vehicles(ctx: ToolContext, args: LimitArgs) -> dict[str, Any]:
    """Scan the tenant's vehicles (keyset pages) against live state; at risk = AT_RISK status or SoC < 30."""
    vehicles: list[Row] = []
    after: int | None = None
    while len(vehicles) < SCAN_MAX:
        page = await ctx.repos.vehicles.find(fleet_id=None, after_id=after, limit=SCAN_PAGE)
        vehicles.extend(page)
        if len(page) < SCAN_PAGE:
            break
        after = page[-1]["id"]
    states = await ctx.store.vehicle_states([v["vin"] for v in vehicles]) if ctx.store is not None else []
    states = states or [None] * len(vehicles)
    risky: list[dict[str, Any]] = []
    no_state = 0
    for v, st in zip(vehicles, states, strict=True):
        soc = st.get("soc_pct") if st else None
        status = st.get("status") if st else None
        if soc is None and status is None:
            no_state += 1
            continue
        if status == "AT_RISK" or (soc is not None and soc < AT_RISK_SOC):
            reason = (
                "AT_RISK_STATUS"
                if status == "AT_RISK"
                else ("CRITICAL_SOC" if soc is not None and soc < CRITICAL_SOC else "LOW_SOC")
            )
            cap = float(v.get("battery_capacity_kwh") or 0)
            nominal = float(v.get("nominal_range_km") or 0)
            kwh = max(0.0, (DEFAULT_TARGET_SOC - (soc or 0.0)) / 100.0 * cap)
            risky.append(
                {
                    "vin": v["vin"],
                    "plate": v.get("plate"),
                    "soc_pct": None if soc is None else round(float(soc), 1),
                    "status": status,
                    "reason": reason,
                    "est_range_km": None if soc is None else round(nominal * float(soc) / 100.0, 1),
                    "kwh_to_target": round(kwh, 1),
                }
            )
    risky.sort(key=lambda r: (r["soc_pct"] is None, r["soc_pct"] or 0.0))
    shown = risky[: args.limit]
    offpeak, peak = await _tariff_prices(ctx)
    kwh_needed = round(sum(r["kwh_to_target"] for r in risky), 1)
    tonight: dict[str, Any] = {
        "target_soc": DEFAULT_TARGET_SOC,
        "vehicles": len(risky),
        "kwh_needed": kwh_needed,
        "peak_price_per_kwh": peak,
        "offpeak_price_per_kwh": offpeak,
    }
    if offpeak is not None:
        tonight.update(
            est_cost_offpeak=round(kwh_needed * offpeak, 2),
            est_cost_peak=round(kwh_needed * peak, 2),
            est_savings=round(kwh_needed * (peak - offpeak), 2),
        )
    return {
        "scanned": len(vehicles),
        "without_live_state": no_state,
        "at_risk_count": len(risky),
        "returned": len(shown),
        "thresholds": {"low_soc_pct": AT_RISK_SOC, "critical_soc_pct": CRITICAL_SOC},
        "items": shown,
        "charge_tonight": tonight,
    }


async def _vehicle(ctx: ToolContext, vin: str) -> Row:
    v = await ctx.repos.vehicles.get_by_vin(vin)
    if v is None:
        raise ToolError(f"vehicle {vin} is not in your fleet")
    return v


def _range_km(v: Row, st: dict[str, Any] | None) -> float | None:
    soc = st.get("soc_pct") if st else None
    if soc is None or v.get("nominal_range_km") is None:
        return None
    return round(float(v["nominal_range_km"]) * float(soc) / 100.0, 1)


async def get_vehicle_state(ctx: ToolContext, args: VinArgs) -> dict[str, Any]:
    v = await _vehicle(ctx, args.vin)
    st = await ctx.store.vehicle_state(v["vin"]) if ctx.store is not None else None
    st = mask_location(ctx.principal.role, st)
    state = None
    if st:
        keep = ("ts", "status", "soc_pct", "soh_pct", "speed_kmh", "charging_state", "charging_kw", "dtc")
        state = {k: st.get(k) for k in keep}
        for k in ("lat", "lon"):
            state[k] = None if st.get(k) is None else round(float(st[k]), 3)
    return {
        "vin": v["vin"],
        "plate": v.get("plate"),
        "model": v.get("model"),
        "battery_capacity_kwh": v.get("battery_capacity_kwh"),
        "nominal_range_km": v.get("nominal_range_km"),
        "state": state,
        "est_range_km": _range_km(v, st),
    }


async def nearest_chargers(ctx: ToolContext, args: NearestArgs) -> dict[str, Any]:
    if ctx.store is None:
        raise ToolError("live state store unavailable")
    v = await _vehicle(ctx, args.vin)
    st = await ctx.store.vehicle_state(v["vin"])
    if st and st.get("lat") is not None and st.get("lon") is not None:
        lat, lon, origin = float(st["lat"]), float(st["lon"]), "live_position"
    else:
        loc = await ctx.repos.vehicles.depot_location(v["id"])
        if loc is None:
            raise ToolError(f"no position known for {v['vin']}")
        (lat, lon), origin = loc, "depot"
    range_km = _range_km(v, st) or DEFAULT_RANGE_KM
    found = await ctx.store.nearest_chargers(lat, lon, max(range_km, 1.0), count=args.limit * 4)
    items = []
    for c in found:
        if c["tenant_id"] not in (None, ctx.repos.tenant_id) and not c["is_public"]:
            continue  # another tenant's private charger
        items.append(
            {
                "charger_id": c["charger_id"],
                "name": c["name"],
                "max_kw": c["max_kw"],
                "distance_km": round(c["distance_km"], 2),
                "reachable": c["distance_km"] < range_km * REACHABLE_FACTOR,
                "price_per_kwh": c["price_per_kwh"],
                "public": c["is_public"],
            }
        )
        if len(items) >= args.limit:
            break
    return {"vin": v["vin"], "origin": origin, "est_range_km": round(range_km, 1), "items": items}


async def get_plan(ctx: ToolContext, args: PlanArgs) -> dict[str, Any]:
    p = await ctx.repos.plans.get(args.plan_id)
    if p is None:
        raise ToolError(f"plan {args.plan_id} not found")
    slots = await ctx.repos.plans.slots(args.plan_id)
    keep = ("id", "vin", "status", "target_soc", "depart_ts", "total_cost", "baseline_cost", "savings")
    return {**{k: p.get(k) for k in keep}, "slot_count": len(slots), "slots": slots}


async def cost_report(ctx: ToolContext, args: CostArgs) -> dict[str, Any]:
    to_ts = ctx.now()
    rows = await ctx.repos.reports.cost_by_day(to_ts - timedelta(days=args.days), to_ts)
    days: list[dict[str, Any]] = []
    for r in rows:
        cost, base = float(r["cost"]), float(r["baseline_cost"])
        days.append(
            {
                "date": str(r["date"]),
                "kwh": round(float(r["kwh"]), 2),
                "cost": round(cost, 2),
                "baseline_cost": round(base, 2),
                "savings": round(base - cost, 2),
            }
        )
    kwh = round(sum(d["kwh"] for d in days), 2)
    cost = round(sum(d["cost"] for d in days), 2)
    base = round(sum(d["baseline_cost"] for d in days), 2)
    savings = round(base - cost, 2)
    return {
        "days_requested": args.days,
        "currency": "INR",
        "totals": {
            "kwh": kwh,
            "cost": cost,
            "baseline_cost": base,
            "savings": savings,
            "savings_pct": round(100.0 * savings / base, 1) if base else 0.0,
        },
        "days": days,
        "baseline": "charge on arrival at peak tariff",
    }


async def list_open_alerts(ctx: ToolContext, args: AlertArgs) -> dict[str, Any]:
    rows = await ctx.repos.alerts.find(
        severity=args.severity, since=None, acked=False, cursor=None, limit=args.limit
    )
    items = [
        {
            "id": a["id"],
            "vin": a["vin"],
            "type": a["type"],
            "severity": a["severity"],
            "ts": a["ts"],
            # free-form, telemetry-derived: kept as an opaque JSON string, never as instructions
            "payload_json": to_jsonable(a.get("payload") or {}),
        }
        for a in rows
    ]
    return {
        "severity": args.severity,
        "returned": len(items),
        "open_total": await ctx.repos.alerts.count_open(),
        "items": items,
    }


async def propose_replan(ctx: ToolContext, args: ReplanArgs) -> dict[str, Any]:
    now = ctx.now()
    depart = args.depart_ts if args.depart_ts.tzinfo else args.depart_ts.replace(tzinfo=UTC)
    if depart <= now + timedelta(minutes=15) or depart > now + timedelta(days=7):
        raise ToolError("depart_ts must be between 15 minutes and 7 days from now")
    owned = {r["vin"] for r in await ctx.repos.vehicles.find_by_vins(args.vins)}  # SQL under RLS
    foreign = [v for v in args.vins if v not in owned]
    if foreign:
        raise ToolError(f"VINs not in your fleet: {', '.join(foreign[:5])}; nothing was proposed")
    row = await ctx.repos.agent.propose(
        action="REPLAN",
        params={"vins": args.vins, "target_soc": args.target_soc, "depart_ts": depart.isoformat()},
        rationale=args.rationale.strip(),
        proposed_by=ctx.run_id,
    )
    return {
        "action_id": row["id"],
        "action": "REPLAN",
        "status": row["status"],
        "vehicles": len(args.vins),
        "target_soc": args.target_soc,
        "depart_ts": depart.isoformat(),
        "note": "Proposal only. A manager must approve it before any charging plan is created.",
    }


# --------------------------------------------------------------------------------------------- registry

TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec(
            "get_fleet_summary",
            "Live fleet counts (vehicles, online, charging, at_risk, avg_soc) plus today's planned charging "
            "cost/savings (INR) and the number of open alerts.",
            NoArgs,
            get_fleet_summary,
        ),
        ToolSpec(
            "list_at_risk_vehicles",
            "Vehicles that should charge soon: status AT_RISK or SoC below 30%, lowest SoC first, with the "
            "energy to reach 80% and a tonight off-peak vs peak cost estimate (INR).",
            LimitArgs,
            list_at_risk_vehicles,
        ),
        ToolSpec(
            "get_vehicle_state",
            "Live state of one vehicle: SoC, SoH, status, charging, DTC codes, estimated range.",
            VinArgs,
            get_vehicle_state,
        ),
        ToolSpec(
            "nearest_chargers",
            "Nearest chargers to a vehicle's live position (or its depot), with distance, power, price and "
            "whether each is reachable on the remaining range.",
            NearestArgs,
            nearest_chargers,
        ),
        ToolSpec("get_plan", "One charging plan with its slots and costs.", PlanArgs, get_plan),
        ToolSpec(
            "cost_report",
            "Charging energy and cost per day for the last N days vs the peak-tariff baseline, with totals "
            "and savings (INR).",
            CostArgs,
            cost_report,
        ),
        ToolSpec(
            "list_open_alerts",
            "Open (unacknowledged) alerts, newest first, optionally filtered by severity.",
            AlertArgs,
            list_open_alerts,
        ),
        ToolSpec(
            "propose_replan",
            "PROPOSE re-planning charging for up to 50 vehicles. Creates a PENDING action that a human must "
            "approve; it does not change any plan. Only use when the user explicitly asks to schedule, "
            "plan or re-plan charging.",
            ReplanArgs,
            propose_replan,
            write=True,
        ),
    )
}

READ_TOOLS = frozenset(n for n, t in TOOLS.items() if not t.write)
ROLE_TOOLS: dict[str, frozenset[str]] = {
    "admin": frozenset(TOOLS),
    "manager": frozenset(TOOLS),
    "finance": frozenset({"get_fleet_summary", "cost_report", "get_plan"}),
    "auditor": frozenset({"get_fleet_summary", "list_open_alerts"}),
}


@dataclass(slots=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    ok: bool
    content: str  # what the model sees (wrapped JSON)
    data: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float = 0.0
    flags: list[str] = field(default_factory=list)


class ToolExecutor:
    """Runs tool calls for one agent query: allowlist, budget, validation, audit row per call."""

    def __init__(self, ctx: ToolContext, *, max_calls: int = 5) -> None:
        self.ctx = ctx
        self.max_calls = max_calls
        self.calls: list[ToolCall] = []
        self.actions: list[dict[str, Any]] = []
        self.allowed = ROLE_TOOLS.get(ctx.principal.role, frozenset())

    @property
    def executed(self) -> int:
        return sum(1 for c in self.calls if c.error != BUDGET_ERROR)

    @property
    def budget_left(self) -> int:
        return max(0, self.max_calls - self.executed)

    def specs(self) -> list[dict[str, Any]]:
        return [TOOLS[n].schema() for n in TOOLS if n in self.allowed]

    def grounding_numbers(self) -> set[float]:
        out: set[float] = set()
        for c in self.calls:
            if c.data is not None:
                numbers_in(to_jsonable(c.data), out)
        return out

    async def call(self, name: str, raw_args: Any) -> ToolCall:
        args_in = raw_args if isinstance(raw_args, dict) else {}
        if self.budget_left <= 0:
            return await self._record(
                ToolCall(name, args_in, False, wrap_error(name, BUDGET_MESSAGE), None, BUDGET_ERROR)
            )
        spec = TOOLS.get(name)
        if spec is None or name not in self.allowed:
            msg = f"tool '{name}' is not available to role {self.ctx.principal.role}"
            return await self._record(ToolCall(name, args_in, False, wrap_error(name, msg), None, msg))
        try:
            args = spec.args.model_validate(args_in)
        except ValidationError as exc:
            msg = "invalid arguments: " + "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'args'}: {e['msg']}" for e in exc.errors()[:5]
            )
            return await self._record(ToolCall(name, args_in, False, wrap_error(name, msg), None, msg))
        t0 = time.perf_counter()
        try:
            data = await spec.handler(self.ctx, args)
        except ToolError as exc:
            call = ToolCall(name, args_in, False, wrap_error(name, str(exc)), None, str(exc))
        else:
            content, flags = wrap_result(name, data)
            call = ToolCall(name, args_in, True, content, data, None, flags=flags)
            if spec.write:
                self.actions.append(
                    {
                        "id": data["action_id"],
                        "action": data["action"],
                        "params": {
                            "vins": args.model_dump()["vins"],
                            "target_soc": data["target_soc"],
                            "depart_ts": data["depart_ts"],
                        },
                        "rationale": args.model_dump()["rationale"].strip(),
                        "status": data["status"],
                    }
                )
        call.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        return await self._record(call)

    async def _record(self, call: ToolCall) -> ToolCall:
        self.calls.append(call)
        nums = sorted(numbers_in(to_jsonable(call.data)))[:300] if call.data is not None else []
        await self.ctx.repos.audit.insert(
            actor_type="agent",
            actor_id=self.ctx.run_id,
            action="AGENT_TOOL",
            resource=f"tool:{call.name}",
            details={
                "name": call.name,
                "args": to_jsonable(call.args),
                "ok": call.ok,
                "error": call.error,
                "result_bytes": len(call.content.encode()),
                "latency_ms": call.latency_ms,
                "call_index": len(self.calls),
                "injection_flags": call.flags[:10],
                "result_numbers": nums,
            },
        )
        return call
