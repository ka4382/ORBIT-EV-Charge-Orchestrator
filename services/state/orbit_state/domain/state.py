"""VehicleState derivation (contracts/api.md) and out-of-order detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import orjson
from orbit_common.events import CanonicalEvent

STATUS_CHARGING = "CHARGING"
STATUS_DRIVING = "DRIVING"
STATUS_IDLE = "IDLE"
STATUS_AT_RISK = "AT_RISK"
STATUS_OFFLINE = "OFFLINE"  # derived by readers from `ts` age; never written by this service

AT_RISK_SOC = 15.0
DRIVING_SPEED_KMH = 2.0


def derive_status(charging_state: str, speed_kmh: float, soc_pct: float | None) -> str:
    """CHARGING wins (a charging vehicle at 10% is not stranded), then AT_RISK, DRIVING, IDLE."""
    if charging_state != "NONE":
        return STATUS_CHARGING
    if soc_pct is not None and soc_pct < AT_RISK_SOC:
        return STATUS_AT_RISK
    if speed_kmh > DRIVING_SPEED_KMH:
        return STATUS_DRIVING
    return STATUS_IDLE


@dataclass(frozen=True, slots=True)
class LastSeen:
    seq: int
    ts: datetime


def is_stale(event: CanonicalEvent, last: LastSeen | None) -> bool:
    """True when `event` is older than what we already hold for this VIN (out-of-order or replayed).
    Ordering is by vehicle `ts`, with `seq` breaking ties so an exact duplicate is also stale."""
    if last is None:
        return False
    if event.ts < last.ts:
        return True
    return event.ts == last.ts and event.seq <= last.seq


def vehicle_state(event: CanonicalEvent) -> dict[str, Any]:
    """VehicleState as a JSON-able dict (the WS `state` payload)."""
    return {
        "vin": event.vin,
        "tenant_id": event.tenant_id,
        "ts": event.ts.isoformat().replace("+00:00", "Z"),
        "seq": event.seq,
        "lat": event.lat,
        "lon": event.lon,
        "speed_kmh": event.speed_kmh,
        "soc_pct": event.soc_pct,
        "soh_pct": event.soh_pct,
        "odo_km": event.odo_km,
        "charging_state": event.charging_state,
        "charging_kw": event.charging_kw,
        "dtc": list(event.dtc),
        "status": derive_status(event.charging_state, event.speed_kmh, event.soc_pct),
    }


def state_value(state: dict[str, Any]) -> bytes:
    """Redis encoding of a VehicleState: one JSON document (typed values, nulls kept) stored with SET EX.

    A 14-field HSET + EXPIRE cost ~30 arguments of redis-py packing per event and was the state service's
    bottleneck; a single SET with a JSON value is 5 arguments and keeps the same information."""
    return orjson.dumps(state)


def last_seen_from_value(raw: bytes | str | None) -> LastSeen | None:
    if not raw:
        return None
    doc = orjson.loads(raw)
    try:
        return LastSeen(int(doc["seq"]), datetime.fromisoformat(str(doc["ts"]).replace("Z", "+00:00")))
    except (KeyError, TypeError, ValueError):
        return None


def state_message(state: dict[str, Any]) -> bytes:
    return orjson.dumps({"type": "state", "data": state})
