"""Redis adapter for the alert engine ports (contracts/redis.md keys)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import redis.asyncio as aioredis

from orbit_alert.application.ports import GeoQuery, SocSample
from orbit_alert.domain.alerts import Alert

GEO_CHARGERS = "geo:chargers"


def charger_key(charger_id: int) -> str:
    return f"charger:{charger_id}"


def soc_key(vin: str) -> str:
    return f"soc:{vin}"


def events_channel(tenant_id: int) -> str:
    return f"events:{tenant_id}"


class RedisAlertStore:
    """Implements AlertDeduper, ChargerIndex and EventBus."""

    def __init__(
        self,
        client: aioredis.Redis,
        *,
        dedupe_ttl_s: int = 900,
        soc_ttl_s: int = 7200,
        soc_entries: int = 24,
        max_commands: int = 1000,
    ) -> None:
        self._r = client
        self._ttl = dedupe_ttl_s
        self._soc_ttl = soc_ttl_s
        self._soc_entries = soc_entries
        self._max = max(3, max_commands)

    @classmethod
    def from_url(cls, url: str, *, pool_size: int = 16, **kw: int) -> RedisAlertStore:
        return cls(aioredis.Redis.from_url(url, max_connections=pool_size, decode_responses=False), **kw)

    async def ping(self) -> bool:
        return bool(await self._r.ping())

    async def close(self) -> None:
        await self._r.aclose()

    # AlertDeduper
    async def claim(self, keys: Sequence[str]) -> list[bool]:
        out: list[bool] = []
        for i in range(0, len(keys), self._max):
            pipe = self._r.pipeline(transaction=False)
            for k in keys[i : i + self._max]:
                pipe.set(k, b"1", nx=True, ex=self._ttl)
            out.extend(bool(v) for v in await pipe.execute())
        return out

    # ChargerIndex
    async def reachable(self, queries: Sequence[GeoQuery]) -> list[bool]:
        out: list[bool] = []
        for i in range(0, len(queries), self._max):
            pipe = self._r.pipeline(transaction=False)
            for q in queries[i : i + self._max]:
                pipe.geosearch(
                    GEO_CHARGERS,
                    longitude=q.lon,
                    latitude=q.lat,
                    radius=q.radius_km,
                    unit="km",
                    count=1,
                    any=True,
                )
            out.extend(bool(v) for v in await pipe.execute())
        return out

    async def chargers_loaded(self) -> bool:
        return bool(await self._r.exists(GEO_CHARGERS))

    async def load_chargers(self, rows: Sequence[dict[str, Any]]) -> int:
        """Populate geo:chargers + charger:{id} from Postgres rows when the API has not done it yet."""
        n = 0
        pipe = self._r.pipeline(transaction=False)
        for row in rows:
            pipe.geoadd(GEO_CHARGERS, (float(row["lon"]), float(row["lat"]), str(row["id"])))
            pipe.hset(
                charger_key(int(row["id"])),
                mapping={
                    "name": str(row.get("name") or ""),
                    "max_kw": _num(row.get("max_kw")),
                    "price_per_kwh": _num(row.get("price_per_kwh")),
                    "is_public": "1" if row.get("is_public") else "0",
                    "tenant_id": "" if row.get("tenant_id") is None else str(row["tenant_id"]),
                },
            )
            n += 2
            if n + 2 > self._max:
                await pipe.execute()
                pipe, n = self._r.pipeline(transaction=False), 0
        if n:
            await pipe.execute()
        return len(rows)

    # EventBus
    async def publish(self, alerts: Sequence[Alert], soc_samples: Sequence[SocSample]) -> None:
        pipe = self._r.pipeline(transaction=False)
        n = 0
        for a in alerts:
            pipe.publish(events_channel(a.tenant_id), a.ws_message())
            n += 1
            if n + 3 > self._max:
                await pipe.execute()
                pipe, n = self._r.pipeline(transaction=False), 0
        for s in soc_samples:
            key = soc_key(s.vin)
            pipe.rpush(key, f"{s.ts_epoch}:{s.soc}")
            pipe.ltrim(key, -self._soc_entries, -1)
            pipe.expire(key, self._soc_ttl)
            n += 3
            if n + 3 > self._max:
                await pipe.execute()
                pipe, n = self._r.pipeline(transaction=False), 0
        if n:
            await pipe.execute()


def _num(v: Any) -> str:
    return "" if v is None else str(float(v))
