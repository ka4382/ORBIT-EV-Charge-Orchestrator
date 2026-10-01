"""StateStore + Redis half of EventPublisher (contracts/redis.md: veh:{vin}, geo:chargers, charger:{id},
pub/sub events:{tenant_id})."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import orjson
import redis.asyncio as aioredis

from ..domain.models import Charger, VehicleState


def _fget(h: dict, key: str, default: float | None = None) -> float | None:
    v = h.get(key)
    if v in (None, "", b""):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class RedisStateStore:
    def __init__(self, url: str) -> None:
        self.r = aioredis.from_url(url, decode_responses=True)

    async def stop(self) -> None:
        await self.r.aclose()

    async def vehicle_state(self, vin: str) -> VehicleState | None:
        raw = await self.r.get(f"veh:{vin}")
        if not raw:
            return None
        h = orjson.loads(raw)
        soc, lat, lon = _fget(h, "soc_pct"), _fget(h, "lat"), _fget(h, "lon")
        if soc is None or lat is None or lon is None:
            return None
        ts = None
        if h.get("ts"):
            try:
                ts = datetime.fromisoformat(str(h["ts"]).replace("Z", "+00:00"))
            except ValueError:
                ts = None
        return VehicleState(soc, lat, lon, _fget(h, "soh_pct"), ts)

    async def public_chargers_near(
        self, lat: float, lon: float, radius_km: float, limit: int = 50
    ) -> list[Charger]:
        """GEOSEARCH geo:chargers (O(log n + m)) then one pipelined HGETALL per hit."""
        hits: list[Any] = await self.r.geosearch(
            "geo:chargers",
            longitude=lon,
            latitude=lat,
            radius=radius_km,
            unit="km",
            withcoord=True,
            sort="ASC",
            count=limit,
        )
        if not hits:
            return []
        pipe = self.r.pipeline()
        for member, _ in hits:
            pipe.hgetall(f"charger:{member}")
        hashes = await pipe.execute()
        out: list[Charger] = []
        for (member, (clon, clat)), h in zip(hits, hashes, strict=True):
            if not h or str(h.get("is_public", "0")).lower() not in ("1", "true", "t"):
                continue
            out.append(
                Charger(
                    int(member),
                    str(h.get("name", member)),
                    float(clat),
                    float(clon),
                    _fget(h, "max_kw", 50.0) or 50.0,
                    "CCS2",
                    True,
                    _fget(h, "price_per_kwh"),
                    None,
                )
            )
        return out

    async def publish(self, tenant_id: int, event: dict[str, object]) -> None:
        await self.r.publish(f"events:{tenant_id}", orjson.dumps(event).decode())
