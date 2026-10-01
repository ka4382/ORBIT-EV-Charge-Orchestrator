"""OEM payload adapters (Adapter pattern). Each maps a raw OEM dict to CanonicalEvent kwargs.
Unknown fields are collected into `extras` (dropped for now, kept for schema evolution)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from .dtc import extract_all
from .events import CanonicalEvent, ChargingState

_KMH_PER_MPH = 1.609344
_EVENT_CODES_B = {
    "HB": "HARSH_BRAKE",
    "CS": "CHARGE_START",
    "CE": "CHARGE_STOP",
    "TS": "TRIP_START",
    "TE": "TRIP_END",
    "DT": "DTC_SET",
}
_CHARGE_MODE_B: dict[int, ChargingState] = {0: "NONE", 1: "AC", 2: "DC"}


def _charging_state(value: Any) -> ChargingState:
    return value if value in ("AC", "DC") else "NONE"


def _dtc_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [c for c in value if c]
    return extract_all(str(value or ""))


class UnknownOemError(ValueError):
    pass


class OemAdapter(ABC):
    code: str

    @abstractmethod
    def to_canonical(self, raw: dict[str, Any]) -> CanonicalEvent: ...


class OemAAdapter(OemAdapter):
    code = "OEM_A"

    def to_canonical(self, raw: dict[str, Any]) -> CanonicalEvent:
        chg = raw.get("chg") or {}
        return CanonicalEvent(
            vin=raw["vin"],
            ts=datetime.fromisoformat(raw["ts"].replace("Z", "+00:00")),
            seq=int(raw["seq"]),
            tenant_id=int(raw["tenant"]),
            oem=self.code,
            lat=float(raw["lat"]),
            lon=float(raw["lon"]),
            speed_kmh=float(raw["speed_kmh"]),
            soc_pct=raw.get("soc"),
            soh_pct=raw.get("soh"),
            odo_km=float(raw["odo_km"]),
            fuel_pct=raw.get("fuel"),
            ambient_c=raw.get("amb_c"),
            dtc=[c for c in raw.get("dtc", []) if c]
            if isinstance(raw.get("dtc"), list)
            else extract_all(str(raw.get("dtc", ""))),
            evt=raw.get("evt"),
            charging_state=_charging_state(chg.get("st")),
            charging_kw=float(chg.get("kw", 0.0)),
        )


class OemBAdapter(OemAdapter):
    code = "OEM_B"

    def to_canonical(self, raw: dict[str, Any]) -> CanonicalEvent:
        veh, pos, bat = raw["vehicle"], raw["pos"], raw.get("battery") or {}
        temp_f = raw.get("temp_f")
        return CanonicalEvent(
            vin=veh["vin"],
            ts=datetime.fromtimestamp(int(raw["t"]) / 1000, tz=UTC),
            seq=int(raw["n"]),
            tenant_id=int(veh["tenant_id"]),
            oem=self.code,
            lat=float(pos["latitude"]),
            lon=float(pos["longitude"]),
            speed_kmh=float(raw["velocity_mph"]) * _KMH_PER_MPH,
            soc_pct=None if bat.get("level") is None else float(bat["level"]) * 100,
            soh_pct=None if bat.get("health") is None else float(bat["health"]) * 100,
            odo_km=float(raw["odometer_mi"]) * _KMH_PER_MPH,
            fuel_pct=None if raw.get("fuel_level") is None else float(raw["fuel_level"]) * 100,
            ambient_c=None if temp_f is None else (float(temp_f) - 32) * 5 / 9,
            dtc=extract_all(str(raw.get("faults", ""))),
            evt=_EVENT_CODES_B.get(raw.get("event_code") or ""),
            charging_state=_CHARGE_MODE_B.get(int(bat.get("charge_mode", 0)), "NONE"),
            charging_kw=float(bat.get("charge_kw", 0.0)),
        )


_REGISTRY: dict[str, OemAdapter] = {a.code: a for a in (OemAAdapter(), OemBAdapter())}


def register(adapter: OemAdapter) -> None:
    """Runtime registration: onboarding a new OEM needs no restart of the ingest service."""
    _REGISTRY[adapter.code] = adapter


def adapter_for(raw: dict[str, Any]) -> OemAdapter:
    code = str(raw.get("oem"))
    try:
        return _REGISTRY[code]
    except KeyError as e:
        raise UnknownOemError(f"unknown OEM format: {code!r}") from e


def normalise(raw: dict[str, Any]) -> CanonicalEvent:
    return adapter_for(raw).to_canonical(raw)
