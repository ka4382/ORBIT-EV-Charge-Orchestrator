"""Postgres adapter: vin -> (vehicle_id, nominal_range_km) lookup, alert inserts, charger export."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import orjson
from psycopg_pool import AsyncConnectionPool

from orbit_alert.application.ports import VehicleInfo
from orbit_alert.domain.alerts import Alert

_LOOKUP_SQL = (
    "SELECT v.vin, v.id, m.nominal_range_km FROM vehicle v JOIN vehicle_model m ON m.id = v.model_id "
    "WHERE v.vin = ANY(%s)"
)
_INSERT_SQL = (
    "INSERT INTO alert (tenant_id, vehicle_id, type, severity, ts, payload) "
    "SELECT t, v, ty, sev, ts, p::jsonb FROM unnest(%s::int[], %s::bigint[], %s::text[], %s::text[], "
    "%s::timestamptz[], %s::text[]) AS u(t, v, ty, sev, ts, p) RETURNING id"
)
_CHARGERS_SQL = "SELECT id, name, lat, lon, max_kw, price_per_kwh, is_public, tenant_id FROM charger"


class PostgresAlertRepo:
    """Implements VehicleLookup and AlertRepository over one psycopg async pool."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @classmethod
    def from_url(cls, url: str, *, min_size: int = 1, max_size: int = 4) -> PostgresAlertRepo:
        return cls(AsyncConnectionPool(url, min_size=min_size, max_size=max_size, open=False))

    async def open(self) -> None:
        await self._pool.open()
        await self._pool.wait()

    async def close(self) -> None:
        await self._pool.close()

    async def lookup(self, vins: Iterable[str]) -> dict[str, VehicleInfo]:
        vins = list(vins)
        if not vins:
            return {}
        async with self._pool.connection() as conn:
            cur = await conn.execute(_LOOKUP_SQL, (vins,))
            rows = await cur.fetchall()
        return {r[0].strip(): VehicleInfo(int(r[1]), None if r[2] is None else float(r[2])) for r in rows}

    async def insert(self, alerts: Sequence[Alert]) -> list[int]:
        if not alerts:
            return []
        cols = (
            [a.tenant_id for a in alerts],
            [a.vehicle_id for a in alerts],
            [a.type for a in alerts],
            [a.severity for a in alerts],
            [a.ts for a in alerts],
            [orjson.dumps(a.payload).decode() for a in alerts],
        )
        async with self._pool.connection() as conn:
            cur = await conn.execute(_INSERT_SQL, cols)
            ids = [int(r[0]) for r in await cur.fetchall()]
            await conn.commit()
        return ids

    async def chargers(self) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(_CHARGERS_SQL)
            rows = await cur.fetchall()
        keys = ("id", "name", "lat", "lon", "max_kw", "price_per_kwh", "is_public", "tenant_id")
        return [dict(zip(keys, r, strict=True)) for r in rows]
