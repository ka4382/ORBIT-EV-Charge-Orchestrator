from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Depends, Query

from ..auth import Principal, require
from ..deps import ReposDep, StoreDep
from ..masking import mask_location
from ..pagination import CursorQuery, LimitQuery, cursor_datetime, decode_cursor, page
from ..problem import ProblemException
from ..schemas import (
    PROBLEM_RESPONSES,
    Items,
    NearestCharger,
    Page,
    RangeForecast,
    TelemetryRow,
    VehicleDetail,
    VehicleItem,
    VehicleState,
)
from ..state import StateStore

router = APIRouter(tags=["vehicles"], responses=PROBLEM_RESPONSES)
AnyRole = Annotated[Principal, Depends(require())]

DEFAULT_RANGE_KM = 50.0  # used when no live state (or ICE) — contracts: "if state missing, use 50 km"
REACHABLE_FACTOR = 0.9
FORECAST_METHOD = "baseline_v0"
STATUSES = {"DRIVING", "IDLE", "CHARGING", "OFFLINE", "AT_RISK"}


async def _vehicle_or_404(repos: ReposDep, vin: str) -> dict[str, Any]:
    v = await repos.vehicles.get_by_vin(vin.upper())
    if v is None:
        raise ProblemException(404, f"vehicle {vin} not found", type_="urn:orbit:problem:not-found")
    return v


def _item(v: dict[str, Any], state: dict[str, Any] | None, role: str) -> VehicleItem:
    return VehicleItem(
        id=v["id"],
        vin=v["vin"],
        plate=v.get("plate"),
        fleet_id=v["fleet_id"],
        model=v["model"],
        powertrain=v["powertrain"],
        state=_state_model(state, role),
    )


def _state_model(state: dict[str, Any] | None, role: str) -> VehicleState | None:
    masked = mask_location(role, state)
    return VehicleState(**masked) if masked else None


def temp_factor(ambient_c: float | None) -> float:
    """Range derating by ambient temperature (baseline heuristic; ML model replaces this)."""
    if ambient_c is None:
        return 1.0
    if ambient_c < 0:
        return 0.7
    if ambient_c < 10:
        return 0.85
    if ambient_c > 35:
        return 0.9
    return 1.0


def remaining_range_km(vehicle: dict[str, Any], state: dict[str, Any] | None) -> float | None:
    nominal = vehicle.get("nominal_range_km")
    soc = state.get("soc_pct") if state else None
    if nominal is None or soc is None:
        return None
    return float(nominal) * float(soc) / 100.0 * temp_factor(state.get("ambient_c") if state else None)


@router.get(
    "/vehicles", response_model=Page[VehicleItem], summary="List vehicles (keyset, joined with live state)"
)
async def list_vehicles(
    repos: ReposDep,
    store: StoreDep,
    principal: AnyRole,
    fleet_id: Annotated[int | None, Query()] = None,
    status: Annotated[str | None, Query(description="filter by live status")] = None,
    limit: Annotated[int, LimitQuery] = 50,
    cursor: Annotated[str | None, CursorQuery] = None,
) -> Page[VehicleItem]:
    if status is not None and status.upper() not in STATUSES:
        raise ProblemException(
            422, f"status must be one of {sorted(STATUSES)}", type_="urn:orbit:problem:validation"
        )
    key = decode_cursor(cursor)
    after_id = int(key[0]) if key else None
    rows = await repos.vehicles.find(fleet_id=fleet_id, after_id=after_id, limit=limit + 1)
    items, next_cursor = page(rows, limit, lambda r: (r["id"],))
    states = await store.vehicle_states([r["vin"] for r in items])
    out = []
    for v, st in zip(items, states, strict=True):
        if status is not None and (st or {"status": "OFFLINE"}).get("status") != status.upper():
            continue
        out.append(_item(v, st, principal.role))
    return Page(items=out, next_cursor=next_cursor)


@router.get("/vehicles/{vin}", response_model=VehicleDetail, summary="Vehicle with model and live state")
async def get_vehicle(vin: str, repos: ReposDep, store: StoreDep, principal: AnyRole) -> VehicleDetail:
    v = await _vehicle_or_404(repos, vin)
    state = await store.vehicle_state(v["vin"])
    return VehicleDetail(**{**v, "state": _state_model(state, principal.role)})


@router.get("/vehicles/{vin}/state", response_model=VehicleState, summary="Live VehicleState from Redis")
async def get_state(vin: str, repos: ReposDep, store: StoreDep, principal: AnyRole) -> VehicleState:
    v = await _vehicle_or_404(repos, vin)
    state = _state_model(await store.vehicle_state(v["vin"]), principal.role)
    if state is None:
        raise ProblemException(404, f"no live state for {v['vin']}", type_="urn:orbit:problem:not-found")
    return state


@router.get(
    "/vehicles/{vin}/telemetry",
    response_model=Page[TelemetryRow],
    summary="Historical telemetry (Timescale), keyset on (ts, seq)",
)
async def get_telemetry(
    vin: str,
    repos: ReposDep,
    principal: AnyRole,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, LimitQuery] = 50,
    cursor: Annotated[str | None, CursorQuery] = None,
) -> Page[TelemetryRow]:
    v = await _vehicle_or_404(repos, vin)
    key = decode_cursor(cursor)
    ck = (cursor_datetime(key[0]), int(key[1])) if key else None
    rows = await repos.vehicles.telemetry(v["vin"], from_ts=from_, to_ts=to, cursor=ck, limit=limit + 1)
    items, next_cursor = page(rows, limit, lambda r: (r["ts"].isoformat(), r["seq"]))
    return Page(
        items=[TelemetryRow(**(mask_location(principal.role, r) or r)) for r in items],
        next_cursor=next_cursor,
    )


@router.get(
    "/vehicles/{vin}/nearest-chargers",
    response_model=Items[NearestCharger],
    summary="Nearest chargers within remaining range (Redis GEOSEARCH)",
)
async def nearest_chargers(
    vin: str,
    repos: ReposDep,
    store: StoreDep,
    principal: AnyRole,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> Items[NearestCharger]:
    v = await _vehicle_or_404(repos, vin)
    state = await store.vehicle_state(v["vin"])
    if state and state.get("lat") is not None and state.get("lon") is not None:
        lat, lon = float(state["lat"]), float(state["lon"])
    else:
        loc = await repos.vehicles.depot_location(v["id"])
        if loc is None:
            raise ProblemException(
                404, f"no position known for {v['vin']}", type_="urn:orbit:problem:not-found"
            )
        lat, lon = loc
    range_km = remaining_range_km(v, state) or DEFAULT_RANGE_KM
    found = await store.nearest_chargers(lat, lon, max(range_km, 1.0), count=limit * 4)
    items: list[NearestCharger] = []
    for c in found:
        if c["tenant_id"] not in (None, repos.tenant_id) and not c["is_public"]:
            continue  # another tenant's private charger
        items.append(
            NearestCharger(
                charger_id=c["charger_id"],
                name=c["name"],
                lat=c["lat"],
                lon=c["lon"],
                max_kw=c["max_kw"],
                distance_km=round(c["distance_km"], 3),
                reachable=c["distance_km"] < range_km * REACHABLE_FACTOR,
                price_per_kwh=c["price_per_kwh"],
            )
        )
        if len(items) >= limit:
            break
    return Items(items=items)


ML_FORECAST_METHOD = "hgb_v1"
ML_MAX_CONFIDENCE = 0.95


async def range_coefficients(store: StateStore, vin: str) -> dict[str, Any] | None:
    """`range:{vin}` JSON written by orbit_ml (contracts/redis.md); None when absent or malformed."""
    try:
        raw: Any = await store.redis.get(f"range:{vin}")
        doc: Any = orjson.loads(raw) if raw else None
        k = float(doc["km_per_soc_pct"])
        mae = float(doc["mae_km"])
    except (orjson.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(k) and k > 0 and math.isfinite(mae) and mae >= 0):
        return None
    return {"km_per_soc_pct": k, "mae_km": mae, "method": str(doc.get("method") or ML_FORECAST_METHOD)}


def ml_confidence(range_km: float, mae_km: float) -> float:
    """1 - relative expected error, capped at 0.95: a 5 km MAE on 150 km is confident, on 10 km it is not."""
    return round(max(0.0, min(ML_MAX_CONFIDENCE, 1.0 - mae_km / max(range_km, 1.0))), 2)


@router.get(
    "/vehicles/{vin}/range-forecast",
    response_model=RangeForecast,
    summary="Remaining range estimate (hgb_v1 coefficients from Redis, else baseline_v0)",
)
async def range_forecast(vin: str, repos: ReposDep, store: StoreDep, principal: AnyRole) -> RangeForecast:
    v = await _vehicle_or_404(repos, vin)
    state = await store.vehicle_state(v["vin"])
    soc = state.get("soc_pct") if state else None
    coeffs = await range_coefficients(store, v["vin"]) if soc is not None else None
    if coeffs is not None and soc is not None:
        # ML path (services/ml publishes range:{vin}); no model is loaded in the API
        ml_range = float(soc) * coeffs["km_per_soc_pct"]
        return RangeForecast(
            range_km=round(ml_range, 1),
            method=coeffs["method"],
            confidence=ml_confidence(ml_range, coeffs["mae_km"]),
        )
    rng = remaining_range_km(v, state)
    if rng is None:
        raise ProblemException(404, f"no SoC known for {v['vin']}", type_="urn:orbit:problem:not-found")
    confidence = 0.5 if state and state.get("ambient_c") is None else 0.6
    return RangeForecast(range_km=round(rng, 1), method=FORECAST_METHOD, confidence=confidence)
