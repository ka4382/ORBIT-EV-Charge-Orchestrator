"""Offline (no API key) strategy: a deterministic regex intent router that calls the same allowlisted tools
as LLM mode and templates an answer using only numbers returned by those tools.

Intent selection looks at the user's question only; tool output (alert payloads, telemetry) never feeds
back into routing, so injected text inside data cannot change which tools run."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .guard import looks_like_injection
from .models import LlmUsage, RunResult
from .prompts import IST
from .tools import DEFAULT_TARGET_SOC, MAX_REPLAN_VINS, ToolCall, ToolExecutor

_VIN_TOKEN = re.compile(r"\b(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{17}\b")
_PLAN_ID = re.compile(r"\bplan\s*(?:#|id|no\.?|number)?\s*#?(\d{1,12})\b", re.I)
_NEAREST = re.compile(r"nearest|closest|nearby|charger|charging (?:point|station)|where .*charge", re.I)
_REPLAN = re.compile(r"\b(schedule|re-?plan|propose|arrange|book)\b", re.I)
_TONIGHT = re.compile(
    r"tonight|overnight|should (?:we |i )?charge|charge (?:next|now)|off-?peak"
    r"|what (?:does|would|will) (?:it|that|this) save",
    re.I,
)
_RISK = re.compile(
    r"at[- ]?risk|\brisk|strand|low (?:soc|battery|charge)|running low|needs? (?:to )?charge", re.I
)
_COST = re.compile(r"\bsav(?:e|ed|es|ing|ings)\b|\bcosts?\b|spen[dt]|\bbill|\bkwh\b|energy|expense", re.I)
_ALERTS = re.compile(r"alerts?|alarms?|critical|warnings?|\bwarn\b|incidents?|faults?|\bdtcs?\b", re.I)
_DAYS = re.compile(r"(\d{1,3})\s*days?", re.I)

HELP = (
    "I can answer: which vehicles should charge tonight and what it saves, which vehicles are at risk, "
    "how much we saved this week, the status of a VIN, the nearest charger for a VIN, open critical "
    "alerts, a fleet overview, or a plan by id. I can also propose a re-plan for approval."
)


def _n(x: Any, dp: int = 1) -> str:
    if x is None:
        return "n/a"
    return f"{float(x):,.{dp}f}" if dp else f"{float(x):,.0f}"


def _rs(x: Any) -> str:
    return "n/a" if x is None else f"₹{float(x):,.2f}"


def _ts(x: Any) -> str:
    """Timestamps for display: ISO-8601 in Asia/Kolkata, minutes precision."""
    if isinstance(x, str):
        try:
            x = datetime.fromisoformat(x)
        except ValueError:
            return x
    if isinstance(x, datetime):
        return x.astimezone(IST).isoformat(timespec="minutes")
    return "n/a"


def _days(q: str) -> int:
    m = _DAYS.search(q)
    if m:
        return max(1, min(int(m.group(1)), 90))
    ql = q.lower()
    if "today" in ql:
        return 1
    if "yesterday" in ql:
        return 2
    if "month" in ql:
        return 30
    return 7


def _severity(q: str) -> str | None:
    ql = q.lower()
    if "critical" in ql:
        return "CRITICAL"
    if "warn" in ql:
        return "WARN"
    if re.search(r"\binfo", ql):
        return "INFO"
    return None


def next_departure(now: datetime) -> datetime:
    """Next 07:00 Asia/Kolkata at least 2 hours away: the default morning departure for a re-plan."""
    local = now.astimezone(IST)
    dep = local.replace(hour=7, minute=0, second=0, microsecond=0)
    while dep <= local + timedelta(hours=2):
        dep += timedelta(days=1)
    return dep


@dataclass(frozen=True)
class Intent:
    name: str
    vin: str | None = None
    plan_id: int | None = None
    days: int = 7
    severity: str | None = None


def route(question: str) -> Intent:
    """Map a question to an intent. Pure function of the question text."""
    q = question.strip()
    if looks_like_injection(q):
        return Intent("refuse")
    if m := _PLAN_ID.search(q):
        return Intent("plan", plan_id=int(m.group(1)))
    vin_m = _VIN_TOKEN.search(q)
    if vin_m:
        vin = vin_m.group(0).upper()
        return Intent("nearest" if _NEAREST.search(q) else "vehicle", vin=vin)
    if _REPLAN.search(q):
        return Intent("replan")
    if _TONIGHT.search(q):
        return Intent("tonight")
    if _RISK.search(q):
        return Intent("risk")
    if _COST.search(q):
        return Intent("cost", days=_days(q))
    if _ALERTS.search(q):
        return Intent("alerts", severity=_severity(q))
    if re.search(r"overview|summary|fleet|online|how many|status|doing", q, re.I):
        return Intent("summary")
    return Intent("help")


def _failed(call: ToolCall) -> str:
    return f"I couldn't get that from {call.name}: {call.error}."


def _risk_lines(items: list[dict[str, Any]]) -> list[str]:
    return [
        f"- {r['vin']}: SoC {_n(r['soc_pct'])}%, ~{_n(r['est_range_km'])} km range, "
        f"{_n(r['kwh_to_target'])} kWh to target ({r['reason']})"
        for r in items
    ]


class OfflineAgent:
    mode = "offline"

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now

    async def run(self, question: str, executor: ToolExecutor) -> RunResult:
        intent = route(question)
        handler = getattr(self, f"_{intent.name}")
        answer: str = await handler(intent, executor)
        stopped = "tool_call_cap" if executor.budget_left == 0 and intent.name == "replan" else None
        return RunResult(answer=answer, usage=LlmUsage(), stopped_reason=stopped)

    async def _refuse(self, intent: Intent, ex: ToolExecutor) -> str:
        return (
            "I can't follow instructions to change my rules, act outside your fleet or skip approval. " + HELP
        )

    async def _help(self, intent: Intent, ex: ToolExecutor) -> str:
        return "I didn't recognise that question. " + HELP

    async def _summary(self, intent: Intent, ex: ToolExecutor) -> str:
        c = await ex.call("get_fleet_summary", {})
        if not c.ok or c.data is None:
            return _failed(c)
        d = c.data
        head = (
            f"Fleet overview: {d['vehicles']} vehicles, {d['online']} online, {d['charging']} charging, "
            f"{d['at_risk']} at risk; average SoC {_n(d['avg_soc'])}%."
            if d["live_state_available"]
            else f"Fleet overview: {d['vehicles']} registered vehicles (live state is unavailable right now)."
        )
        return (
            f"{head} Open alerts: {d['open_alerts']}. Today: {_n(d['kwh_today'])} kWh, planned cost "
            f"{_rs(d['cost_today'])}, savings {_rs(d['savings_today'])}."
        )

    async def _risk(self, intent: Intent, ex: ToolExecutor) -> str:
        c = await ex.call("list_at_risk_vehicles", {"limit": 10})
        if not c.ok or c.data is None:
            return _failed(c)
        d = c.data
        if not d["at_risk_count"]:
            return (
                f"No vehicles are at risk right now: none of the {d['scanned']} vehicles is below "
                f"{_n(d['thresholds']['low_soc_pct'], 0)}% SoC or flagged AT_RISK."
            )
        lines = [
            f"{d['at_risk_count']} vehicle(s) at risk (SoC below {_n(d['thresholds']['low_soc_pct'], 0)}% "
            f"or flagged AT_RISK), lowest first (showing {d['returned']}):"
        ]
        return "\n".join(lines + _risk_lines(d["items"]))

    async def _tonight(self, intent: Intent, ex: ToolExecutor) -> str:
        c = await ex.call("list_at_risk_vehicles", {"limit": 10})
        if not c.ok or c.data is None:
            return _failed(c)
        d, t = c.data, c.data["charge_tonight"]
        if not d["at_risk_count"]:
            return (
                f"No vehicle needs to charge tonight: none of the {d['scanned']} vehicles is below "
                f"{_n(d['thresholds']['low_soc_pct'], 0)}% SoC or flagged AT_RISK."
            )
        lines = [f"Charge these {t['vehicles']} vehicle(s) tonight (showing {d['returned']}):"]
        lines += _risk_lines(d["items"])
        if t.get("est_savings") is not None:
            lines.append(
                f"Bringing them to {_n(t['target_soc'], 0)}% needs {_n(t['kwh_needed'])} kWh. Off-peak at "
                f"{_rs(t['offpeak_price_per_kwh'])}/kWh that costs about {_rs(t['est_cost_offpeak'])} vs "
                f"{_rs(t['est_cost_peak'])} at peak ({_rs(t['peak_price_per_kwh'])}/kWh): it saves about "
                f"{_rs(t['est_savings'])}."
            )
        else:
            lines.append(
                f"Bringing them to {_n(t['target_soc'], 0)}% needs {_n(t['kwh_needed'])} kWh "
                "(no tariff configured, so no savings estimate)."
            )
        return "\n".join(lines)

    async def _replan(self, intent: Intent, ex: ToolExecutor) -> str:
        c = await ex.call("list_at_risk_vehicles", {"limit": MAX_REPLAN_VINS})
        if not c.ok or c.data is None:
            return _failed(c)
        vins = [r["vin"] for r in c.data["items"]]
        if not vins:
            return "No vehicles are at risk, so there is nothing to re-plan. No action was proposed."
        now = self._now() if self._now else ex.ctx.now()
        depart = next_departure(now)
        p = await ex.call(
            "propose_replan",
            {
                "vins": vins,
                "target_soc": DEFAULT_TARGET_SOC,
                "depart_ts": depart.isoformat(),
                "rationale": "At-risk vehicles (low SoC or AT_RISK status) charged off-peak before the "
                "morning departure.",
            },
        )
        if not p.ok or p.data is None:
            return _failed(p)
        r = p.data
        return (
            f"Proposed action #{r['action_id']} (status {r['status']}): re-plan charging for "
            f"{r['vehicles']} at-risk vehicle(s) to {_n(r['target_soc'], 0)}% before "
            f"{_ts(depart)}. Nothing changes until a manager approves it."
        )

    async def _cost(self, intent: Intent, ex: ToolExecutor) -> str:
        c = await ex.call("cost_report", {"days": intent.days})
        if not c.ok or c.data is None:
            return _failed(c)
        t = c.data["totals"]
        span = f"In the last {c.data['days_requested']} day(s)"
        if not c.data["days"]:
            return f"{span} there were no recorded charging sessions, so nothing was spent or saved."
        return (
            f"{span} the fleet charged {_n(t['kwh'], 2)} kWh for {_rs(t['cost'])} against "
            f"{_rs(t['baseline_cost'])} at the peak-tariff baseline: savings of {_rs(t['savings'])} "
            f"({_n(t['savings_pct'])}%)."
        )

    async def _alerts(self, intent: Intent, ex: ToolExecutor) -> str:
        args: dict[str, Any] = {"limit": 10}
        if intent.severity:
            args["severity"] = intent.severity
        c = await ex.call("list_open_alerts", args)
        if not c.ok or c.data is None:
            return _failed(c)
        d = c.data
        label = f"{intent.severity.lower()} " if intent.severity else ""
        if not d["items"]:
            return f"There are no open {label}alerts ({d['open_total']} open alerts in total)."
        lines = [f"{d['returned']} open {label}alert(s) ({d['open_total']} open in total), newest first:"]
        lines += [
            f"- #{a['id']} {a['type']} ({a['severity']}) on {a['vin']} at {_ts(a['ts'])}" for a in d["items"]
        ]
        return "\n".join(lines)

    async def _vehicle(self, intent: Intent, ex: ToolExecutor) -> str:
        c = await ex.call("get_vehicle_state", {"vin": intent.vin})
        if not c.ok or c.data is None:
            return _failed(c)
        d, s = c.data, c.data["state"]
        head = f"{d['vin']} ({d['model']}, plate {d['plate']})"
        if not s:
            return f"{head} is in your fleet but has no live state right now."
        dtc = ", ".join(s.get("dtc") or []) or "none"
        return (
            f"{head}: status {s['status']}, SoC {_n(s['soc_pct'])}%, SoH {_n(s['soh_pct'])}%, "
            f"est. range {_n(d['est_range_km'])} km, charging {s['charging_state']} at "
            f"{_n(s['charging_kw'])} kW. Last update {_ts(s['ts'])}. DTCs: {dtc}."
        )

    async def _nearest(self, intent: Intent, ex: ToolExecutor) -> str:
        c = await ex.call("nearest_chargers", {"vin": intent.vin, "limit": 3})
        if not c.ok or c.data is None:
            return _failed(c)
        d = c.data
        origin = "live position" if d["origin"] == "live_position" else "depot"
        if not d["items"]:
            return f"No chargers found within {_n(d['est_range_km'])} km of {d['vin']}'s {origin}."
        lines = [
            f"Nearest chargers to {d['vin']} (from its {origin}, est. range {_n(d['est_range_km'])} km):"
        ]
        for ch in d["items"]:
            price = f"{_rs(ch['price_per_kwh'])}/kWh" if ch["price_per_kwh"] is not None else "depot tariff"
            reach = "reachable" if ch["reachable"] else "NOT reachable on current charge"
            lines.append(
                f"- {ch['name']} (#{ch['charger_id']}): {_n(ch['distance_km'], 2)} km, "
                f"{_n(ch['max_kw'], 0)} kW, {price}, {reach}"
            )
        return "\n".join(lines)

    async def _plan(self, intent: Intent, ex: ToolExecutor) -> str:
        c = await ex.call("get_plan", {"plan_id": intent.plan_id})
        if not c.ok or c.data is None:
            return _failed(c)
        p = c.data
        money = (
            f" Cost {_rs(p['total_cost'])} vs baseline {_rs(p['baseline_cost'])}, saving {_rs(p['savings'])}."
            if p.get("total_cost") is not None
            else " Not priced yet."
        )
        return (
            f"Plan #{p['id']} for {p['vin']}: status {p['status']}, target {_n(p['target_soc'], 0)}% by "
            f"{_ts(p['depart_ts'])}, {p['slot_count']} slot(s).{money}"
        )
