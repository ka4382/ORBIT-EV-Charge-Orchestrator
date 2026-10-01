"""PlanRepository over psycopg (async pool). Tariff and depot rows are cached in-process: the nightly run
plans thousands of vehicles that share a handful of depots/tariffs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from psycopg_pool import AsyncConnectionPool

from ..domain.models import Charger, Depot, Vehicle, VehicleModel
from ..domain.schedule import Slot, TariffSlot

_VEHICLE_SQL = """
SELECT v.id, v.vin, v.tenant_id, v.fleet_id,
       m.id, m.name, m.powertrain, m.battery_capacity_kwh, m.nominal_range_km, m.max_ac_kw, m.max_dc_kw,
       d.id, d.lat, d.lon, d.grid_capacity_kw, d.tariff_id, t.timezone
FROM vehicle v
JOIN vehicle_model m ON m.id = v.model_id
JOIN fleet f ON f.id = v.fleet_id
LEFT JOIN depot d ON d.id = f.depot_id
LEFT JOIN tariff t ON t.id = d.tariff_id
"""


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


class PostgresPlanRepository:
    def __init__(self, dsn: str, pool_size: int = 4) -> None:
        self.pool = AsyncConnectionPool(dsn, min_size=1, max_size=pool_size, open=False)
        self._tariffs: dict[int, tuple[TariffSlot, ...]] = {}

    async def start(self) -> None:
        await self.pool.open()

    async def stop(self) -> None:
        await self.pool.close()

    async def _tariff(self, tariff_id: int | None) -> tuple[TariffSlot, ...]:
        if tariff_id is None:
            return ()
        if tariff_id not in self._tariffs:
            async with self.pool.connection() as conn:
                rows = await (
                    await conn.execute(
                        "SELECT dow, start_min, end_min, price_per_kwh FROM tariff_slot WHERE tariff_id = %s "
                        "ORDER BY dow, start_min",
                        (tariff_id,),
                    )
                ).fetchall()
            self._tariffs[tariff_id] = tuple(
                TariffSlot(int(d), int(s), int(e), float(p)) for d, s, e, p in rows
            )
        return self._tariffs[tariff_id]

    async def _to_vehicle(self, r: tuple) -> Vehicle:
        model = VehicleModel(r[4], r[5], r[6], _f(r[7]), _f(r[8]), _f(r[9]), _f(r[10]))
        depot = None
        if r[11] is not None:
            depot = Depot(
                r[11],
                float(r[12]),
                float(r[13]),
                float(r[14]),
                await self._tariff(r[15]),
                r[16] or "Asia/Kolkata",
            )
        return Vehicle(int(r[0]), r[1].strip(), int(r[2]), int(r[3]), model, depot)

    async def get_vehicle_by_vin(self, vin: str) -> Vehicle | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(_VEHICLE_SQL + " WHERE v.vin = %s", (vin,))).fetchone()
        return await self._to_vehicle(row) if row else None

    async def list_bevs(self, tenant_id: int) -> list[Vehicle]:
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    _VEHICLE_SQL + " WHERE v.tenant_id = %s AND m.powertrain = 'BEV' ORDER BY v.id",
                    (tenant_id,),
                )
            ).fetchall()
        return [await self._to_vehicle(r) for r in rows]

    async def list_tenants(self) -> list[int]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute("SELECT id FROM tenant ORDER BY id")).fetchall()
        return [int(r[0]) for r in rows]

    async def depot_chargers(self, depot_id: int) -> list[Charger]:
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT id, name, lat, lon, max_kw, connector, is_public, price_per_kwh, depot_id "
                    "FROM charger WHERE depot_id = %s ORDER BY max_kw DESC, id",
                    (depot_id,),
                )
            ).fetchall()
        return [
            Charger(int(r[0]), r[1], float(r[2]), float(r[3]), float(r[4]), r[5], bool(r[6]), _f(r[7]), r[8])
            for r in rows
        ]

    async def create_plan(
        self, vehicle_id: int, target_soc: float, depart_ts: datetime, requested_by: str
    ) -> int:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "INSERT INTO charging_plan(vehicle_id, target_soc, depart_ts, requested_by, status) "
                    "VALUES (%s, %s, %s, %s, 'PENDING') RETURNING id",
                    (vehicle_id, target_soc, depart_ts, requested_by),
                )
            ).fetchone()
        assert row is not None
        return int(row[0])

    async def save_plan(
        self,
        plan_id: int,
        status: str,
        charger_id: int | None,
        slots: Sequence[Slot],
        total_cost: float | None,
        total_kwh: float | None,
        baseline_cost: float | None,
        achievable_soc: float | None = None,
    ) -> bool:
        async with self.pool.connection() as conn, conn.transaction():
            exists = await conn.execute("SELECT 1 FROM charging_plan WHERE id = %s FOR UPDATE", (plan_id,))
            if await exists.fetchone() is None:
                return False  # request published before its INSERT committed (or the API rolled back)
            await conn.execute("DELETE FROM charging_slot WHERE plan_id = %s", (plan_id,))
            if slots and charger_id is not None:
                async with conn.cursor() as cur:
                    await cur.executemany(
                        "INSERT INTO charging_slot(plan_id, charger_id, start_ts, end_ts, kw, kwh, cost) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        [(plan_id, charger_id, s.start_ts, s.end_ts, s.kw, s.kwh, s.cost) for s in slots],
                    )
            await conn.execute(
                "UPDATE charging_plan SET total_cost = %s, total_kwh = %s, baseline_cost = %s, status = %s, "
                "achievable_soc = %s WHERE id = %s",
                (total_cost, total_kwh, baseline_cost, status, achievable_soc, plan_id),
            )
        return True

    async def mark_failed(self, plan_id: int) -> None:
        await self.save_plan(plan_id, "FAILED", None, [], None, None, None)
