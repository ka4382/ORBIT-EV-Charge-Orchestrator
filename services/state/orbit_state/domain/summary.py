"""In-memory per-tenant fleet tracker feeding the 5-second `summary:{tenant_id}` snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import orjson
from orbit_common.events import CanonicalEvent

from orbit_state.domain.state import STATUS_AT_RISK, STATUS_CHARGING, derive_status

MAX_CHARGE_GAP_S = 60.0  # cap the integration step so a vehicle that vanished mid-charge does not inflate kWh


@dataclass(slots=True)
class VehicleSnap:
    ts: float  # epoch seconds of the last vehicle event
    soc: float | None
    status: str
    charging_kw: float


@dataclass(slots=True)
class TenantAgg:
    vehicles: dict[str, VehicleSnap]
    kwh_today: float = 0.0
    day: date | None = None


class FleetTracker:
    def __init__(self, online_window_s: float = 120.0) -> None:
        self.online_window_s = online_window_s
        self._tenants: dict[int, TenantAgg] = {}

    def observe(self, event: CanonicalEvent) -> None:
        """Record the latest snapshot for the VIN and integrate charging energy since the previous event."""
        agg = self._tenants.get(event.tenant_id)
        if agg is None:
            agg = self._tenants[event.tenant_id] = TenantAgg(vehicles={})
        day = event.ts.date()
        if agg.day != day:
            agg.day, agg.kwh_today = day, 0.0
        ts = event.ts.timestamp()
        prev = agg.vehicles.get(event.vin)
        if prev is not None and prev.charging_kw > 0 and ts > prev.ts:
            agg.kwh_today += prev.charging_kw * min(ts - prev.ts, MAX_CHARGE_GAP_S) / 3600.0
        elif prev is None and event.charging_kw > 0:
            agg.kwh_today += event.charging_kw / 3600.0  # first sighting: assume 1 Hz
        agg.vehicles[event.vin] = VehicleSnap(
            ts=ts,
            soc=event.soc_pct,
            status=derive_status(event.charging_state, event.speed_kmh, event.soc_pct),
            charging_kw=event.charging_kw,
        )

    def tenants(self) -> list[int]:
        return list(self._tenants)

    def summary(self, tenant_id: int, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        agg = self._tenants.get(tenant_id)
        if agg is None:
            return _empty(tenant_id, now)
        cutoff = now.timestamp() - self.online_window_s
        # Forget vehicles not heard from within the online window. After a consumer-group rebalance this
        # replica stops receiving the partitions it lost; pruning drops those vehicles from its partial
        # summary within `online_window_s`, so replicas never double-count for longer than that. It also
        # bounds memory to vehicles that are actually reporting.
        stale = [vin for vin, snap in agg.vehicles.items() if snap.ts < cutoff]
        for vin in stale:
            del agg.vehicles[vin]
        online = charging = at_risk = 0
        soc_sum, soc_n = 0.0, 0
        for snap in agg.vehicles.values():
            if snap.ts >= cutoff:
                online += 1
            if snap.status == STATUS_CHARGING:
                charging += 1
            elif snap.status == STATUS_AT_RISK:
                at_risk += 1
            if snap.soc is not None:
                soc_sum += snap.soc
                soc_n += 1
        return {
            "tenant_id": tenant_id,
            "vehicles": len(agg.vehicles),
            "online": online,
            "charging": charging,
            "at_risk": at_risk,
            "avg_soc": round(soc_sum / soc_n, 2) if soc_n else None,
            "soc_n": soc_n,  # lets the API weight avg_soc when merging partial summaries
            "kwh_today": round(agg.kwh_today, 3),
            "ts": now.isoformat().replace("+00:00", "Z"),
        }


def _empty(tenant_id: int, now: datetime) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "vehicles": 0,
        "online": 0,
        "charging": 0,
        "at_risk": 0,
        "avg_soc": None,
        "soc_n": 0,
        "kwh_today": 0.0,
        "ts": now.isoformat().replace("+00:00", "Z"),
    }


def summary_hash(summary: dict[str, Any]) -> dict[str, str]:
    return {k: ("" if v is None else str(v)) for k, v in summary.items()}


def summary_message(summary: dict[str, Any]) -> bytes:
    return orjson.dumps({"type": "summary", "data": summary})
