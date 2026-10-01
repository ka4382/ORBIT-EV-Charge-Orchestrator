"""Redis read-side adapter: vehicle state, fleet summary, charger geo index, pub/sub (contracts/redis.md)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import orjson
from redis.asyncio import Redis

GEO_CHARGERS = "geo:chargers"
STATE_FLOAT_FIELDS = ("lat", "lon", "speed_kmh", "soc_pct", "soh_pct", "odo_km", "charging_kw", "ambient_c")
STATE_INT_FIELDS = ("seq", "tenant_id")
SUMMARY_INT_FIELDS = ("vehicles", "online", "charging", "at_risk", "open_alerts")
SUMMARY_FLOAT_FIELDS = ("avg_soc", "kwh_today", "cost_today", "savings_today")


def _num(value: Any, cast: type) -> Any:
    if value is None or value == "":
        return None
    try:
        return cast(float(value)) if cast is int else cast(value)
    except (TypeError, ValueError):
        return None


def parse_state(raw: dict[str, Any] | bytes | str | None) -> dict[str, Any] | None:
    """`veh:{vin}` value (JSON document, see contracts/redis.md) → typed VehicleState dict.
    A dict (legacy HASH encoding) is accepted too. Unknown fields pass through untouched."""
    if not raw:
        return None
    if isinstance(raw, bytes | str):
        try:
            raw = orjson.loads(raw)
        except orjson.JSONDecodeError:
            return None
        if not isinstance(raw, dict):
            return None
    out: dict[str, Any] = dict(raw)
    for f in STATE_FLOAT_FIELDS:
        if f in out:
            out[f] = _num(out[f], float)
    for f in STATE_INT_FIELDS:
        if f in out:
            out[f] = _num(out[f], int)
    dtc = out.get("dtc")
    if isinstance(dtc, str):
        try:
            parsed = orjson.loads(dtc) if dtc else []
        except orjson.JSONDecodeError:
            parsed = [c for c in dtc.split(",") if c]
        out["dtc"] = parsed if isinstance(parsed, list) else []
    elif dtc is None:
        out["dtc"] = []
    out.setdefault("charging_state", "NONE")
    out.setdefault("charging_kw", 0.0)
    out.setdefault("status", "OFFLINE")
    return out


def parse_summary(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    out: dict[str, Any] = {}
    for f in SUMMARY_INT_FIELDS:
        out[f] = _num(raw.get(f), int) or 0
    for f in SUMMARY_FLOAT_FIELDS:
        out[f] = _num(raw.get(f), float) or 0.0
    return out


def merge_summaries(parts: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Sum counts across replicas; avg_soc is weighted by each part's number of vehicles with SoC."""
    parsed = [p for p in (parse_summary(x) for x in parts) if p]
    if not parsed:
        return None
    out: dict[str, Any] = {f: 0 for f in SUMMARY_INT_FIELDS}
    out.update({f: 0.0 for f in SUMMARY_FLOAT_FIELDS})
    soc_weight = 0
    soc_sum = 0.0
    for raw, p in zip(parts, parsed, strict=True):
        for f in SUMMARY_INT_FIELDS:
            out[f] += p[f]
        for f in ("kwh_today", "cost_today", "savings_today"):
            out[f] += p[f]
        n = _num(raw.get("soc_n"), int)
        n = p["vehicles"] if n is None else n
        if n and p["avg_soc"]:
            soc_sum += p["avg_soc"] * n
            soc_weight += n
    out["avg_soc"] = round(soc_sum / soc_weight, 2) if soc_weight else 0.0
    out["kwh_today"] = round(out["kwh_today"], 3)
    return out


class StateStore:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def ping(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except Exception:
            return False

    async def vehicle_state(self, vin: str) -> dict[str, Any] | None:
        raw: Any = await self.redis.get(f"veh:{vin}")
        return parse_state(raw)

    async def vehicle_states(self, vins: Sequence[str]) -> list[dict[str, Any] | None]:
        if not vins:
            return []
        results = await self.redis.mget([f"veh:{vin}" for vin in vins])
        return [parse_state(r) for r in results]

    async def summary(self, tenant_id: int) -> dict[str, Any] | None:
        """Merge the live partial summaries written by each state replica (contracts/redis.md)."""
        members: Any = await self.redis.smembers(f"summary:{tenant_id}:parts")
        parts: list[Any] = []
        if members:
            async with self.redis.pipeline(transaction=False) as pipe:
                for m in members:
                    rid = m.decode() if isinstance(m, bytes) else m
                    pipe.hgetall(f"summary:{tenant_id}:part:{rid}")
                parts = [p for p in await pipe.execute() if p]
        if not parts:
            legacy: Any = await self.redis.hgetall(f"summary:{tenant_id}")
            return parse_summary(legacy)
        merged = merge_summaries(parts)
        registered = await self.redis.scard(f"tenant:{tenant_id}:vins")
        if merged is not None and registered:
            merged["vehicles"] = int(registered)  # exact: VINs ever registered, independent of replicas
        return merged

    async def load_chargers(self, chargers: Iterable[dict[str, Any]]) -> int:
        """Populate `geo:chargers` + `charger:{id}` hashes (called at API startup from the charger table)."""
        n = 0
        async with self.redis.pipeline(transaction=False) as pipe:
            for c in chargers:
                pipe.geoadd(GEO_CHARGERS, (float(c["lon"]), float(c["lat"]), str(c["id"])))
                pipe.hset(
                    f"charger:{c['id']}",
                    mapping={
                        "name": c["name"],
                        "max_kw": str(c["max_kw"]),
                        "price_per_kwh": "" if c.get("price_per_kwh") is None else str(c["price_per_kwh"]),
                        "is_public": "1" if c.get("is_public") else "0",
                        "tenant_id": "" if c.get("tenant_id") is None else str(c["tenant_id"]),
                        "lat": str(c["lat"]),
                        "lon": str(c["lon"]),
                    },
                )
                n += 1
            await pipe.execute()
        return n

    async def nearest_chargers(
        self, lat: float, lon: float, radius_km: float, count: int
    ) -> list[dict[str, Any]]:
        """GEOSEARCH BYRADIUS ... WITHDIST WITHCOORD ASC, joined with `charger:{id}` hashes via a pipeline."""
        found: Any = await self.redis.geosearch(
            GEO_CHARGERS,
            longitude=lon,
            latitude=lat,
            radius=radius_km,
            unit="km",
            withdist=True,
            withcoord=True,
            sort="ASC",
            count=count,
        )
        if not found:
            return []
        async with self.redis.pipeline(transaction=False) as pipe:
            for member, _dist, _coord in found:
                pipe.hgetall(f"charger:{member}")
            infos = await pipe.execute()
        out: list[dict[str, Any]] = []
        for (member, dist, coord), info in zip(found, infos, strict=True):
            info = info or {}
            price = _num(info.get("price_per_kwh"), float)
            tenant = _num(info.get("tenant_id"), int)
            out.append(
                {
                    "charger_id": int(member),
                    "name": info.get("name", f"charger-{member}"),
                    "lon": float(coord[0]),
                    "lat": float(coord[1]),
                    "max_kw": _num(info.get("max_kw"), float) or 0.0,
                    "distance_km": float(dist),
                    "price_per_kwh": price,
                    "tenant_id": tenant,
                    "is_public": info.get("is_public") == "1",
                }
            )
        return out

    async def publish(self, tenant_id: int, message: dict[str, Any]) -> None:
        await self.redis.publish(f"events:{tenant_id}", orjson.dumps(message).decode())
