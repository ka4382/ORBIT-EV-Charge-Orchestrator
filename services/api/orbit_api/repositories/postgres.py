"""asyncpg implementations. All queries run on a connection where `app.tenant_id` is set (RLS) and
additionally filter by tenant explicitly where the column exists — defence in depth. No N+1: joins only."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import asyncpg

from .base import Repos, Row

PEAK_PRICE_PER_KWH = 12.5  # baseline "charge on arrival at peak tariff" used when baseline_cost is not stored

_VEHICLE_SELECT = """
SELECT v.id, v.vin, v.plate, v.fleet_id, v.tenant_id, v.created_at,
       m.name AS model, m.powertrain, m.battery_capacity_kwh, m.nominal_range_km, o.code AS oem
FROM vehicle v
JOIN vehicle_model m ON m.id = v.model_id
JOIN oem o ON o.id = m.oem_id
"""


def _rows(records: list[asyncpg.Record]) -> list[Row]:
    return [dict(r) for r in records]


class _Base:
    def __init__(self, conn: asyncpg.Connection, tenant_id: int) -> None:
        self.conn = conn
        self.tenant_id = tenant_id


class PgFleetRepository(_Base):
    async def find(self) -> list[Row]:
        return _rows(
            await self.conn.fetch(
                "SELECT f.id, f.name, f.depot_id, count(v.id)::int AS vehicle_count "
                "FROM fleet f LEFT JOIN vehicle v ON v.fleet_id = f.id "
                "WHERE f.tenant_id = $1 GROUP BY f.id ORDER BY f.id",
                self.tenant_id,
            )
        )

    async def vins(self, fleet_id: int) -> set[str]:
        rows = await self.conn.fetch(
            "SELECT vin FROM vehicle WHERE tenant_id = $1 AND fleet_id = $2", self.tenant_id, fleet_id
        )
        return {r["vin"] for r in rows}


class PgVehicleRepository(_Base):
    async def find(self, *, fleet_id: int | None, after_id: int | None, limit: int) -> list[Row]:
        return _rows(
            await self.conn.fetch(
                _VEHICLE_SELECT + "WHERE v.tenant_id = $1 AND ($2::int IS NULL OR v.fleet_id = $2) "
                "AND ($3::bigint IS NULL OR v.id > $3) ORDER BY v.id LIMIT $4",
                self.tenant_id,
                fleet_id,
                after_id,
                limit,
            )
        )

    async def get_by_vin(self, vin: str) -> Row | None:
        r = await self.conn.fetchrow(
            _VEHICLE_SELECT + "WHERE v.tenant_id = $1 AND v.vin = $2", self.tenant_id, vin
        )
        return dict(r) if r else None

    async def count(self) -> int:
        return int(
            await self.conn.fetchval("SELECT count(*) FROM vehicle WHERE tenant_id = $1", self.tenant_id)
        )

    async def depot_location(self, vehicle_id: int) -> tuple[float, float] | None:
        r = await self.conn.fetchrow(
            "SELECT d.lat, d.lon FROM vehicle v JOIN fleet f ON f.id = v.fleet_id "
            "JOIN depot d ON d.id = f.depot_id "
            "WHERE v.tenant_id = $1 AND v.id = $2",
            self.tenant_id,
            vehicle_id,
        )
        return (float(r["lat"]), float(r["lon"])) if r else None

    async def find_by_vins(self, vins: Sequence[str]) -> list[Row]:
        # explicit tenant filter + RLS: a VIN of another tenant is simply not returned
        return _rows(
            await self.conn.fetch(
                "SELECT id, vin FROM vehicle WHERE tenant_id = $1 AND vin = ANY($2::text[]) ORDER BY id",
                self.tenant_id,
                list(vins),
            )
        )

    async def telemetry(
        self,
        vin: str,
        *,
        from_ts: datetime | None,
        to_ts: datetime | None,
        cursor: tuple[datetime, int] | None,
        limit: int,
    ) -> list[Row]:
        c_ts, c_seq = cursor if cursor else (None, None)
        return _rows(
            await self.conn.fetch(
                "SELECT ts, vin, seq, lat, lon, speed_kmh, soc_pct, soh_pct, odo_km, ambient_c, "
                "charging_state, "
                "charging_kw, dtc, evt FROM telemetry WHERE tenant_id = $1 AND vin = $2 "
                "AND ($3::timestamptz IS NULL OR ts >= $3) AND ($4::timestamptz IS NULL OR ts <= $4) "
                "AND ($5::timestamptz IS NULL OR (ts, seq) < ($5, $6::bigint)) "
                "ORDER BY ts DESC, seq DESC LIMIT $7",
                self.tenant_id,
                vin,
                from_ts,
                to_ts,
                c_ts,
                c_seq,
                limit,
            )
        )


_PLAN_SELECT = """
SELECT p.id, v.vin, p.status, p.target_soc, p.depart_ts, p.created_ts, p.total_cost, p.baseline_cost,
       p.total_kwh, p.achievable_soc,
       CASE WHEN p.total_cost IS NOT NULL THEN
            COALESCE(p.baseline_cost, p.total_kwh * $PEAK) - p.total_cost END AS savings
FROM charging_plan p JOIN vehicle v ON v.id = p.vehicle_id
""".replace("$PEAK", str(PEAK_PRICE_PER_KWH))


class PgPlanRepository(_Base):
    async def totals_today(self) -> tuple[float, float]:
        row = await self.conn.fetchrow(
            """SELECT coalesce(sum(total_cost), 0) AS cost,
                      coalesce(sum(baseline_cost - total_cost), 0) AS savings
               FROM charging_plan
               WHERE tenant_id = $1 AND status IN ('READY', 'ACTIVE', 'DONE')
                 AND created_ts >= (
                     date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata'
                 )""",
            self.tenant_id,
        )
        return (float(row["cost"]), float(row["savings"])) if row else (0.0, 0.0)

    async def create(
        self, *, vehicle_id: int, target_soc: float, depart_ts: datetime, requested_by: str
    ) -> int:
        return int(
            await self.conn.fetchval(
                "INSERT INTO charging_plan (vehicle_id, target_soc, depart_ts, status, requested_by) "
                "VALUES ($1, $2, $3, 'PENDING', $4) RETURNING id",
                vehicle_id,
                target_soc,
                depart_ts,
                requested_by,
            )
        )

    async def get(self, plan_id: int) -> Row | None:
        r = await self.conn.fetchrow(
            _PLAN_SELECT + "WHERE v.tenant_id = $1 AND p.id = $2", self.tenant_id, plan_id
        )
        return dict(r) if r else None

    async def slots(self, plan_id: int) -> list[Row]:
        return _rows(
            await self.conn.fetch(
                "SELECT s.charger_id, s.start_ts, s.end_ts, s.kw, s.kwh, s.cost, "
                "COALESCE(c.price_per_kwh, CASE WHEN s.kwh > 0 THEN s.cost / s.kwh END) AS price_per_kwh "
                "FROM charging_slot s JOIN charger c ON c.id = s.charger_id "
                "WHERE s.plan_id = $1 ORDER BY s.start_ts",
                plan_id,
            )
        )

    async def find(
        self, *, vin: str | None, status: str | None, cursor: tuple[datetime, int] | None, limit: int
    ) -> list[Row]:
        c_ts, c_id = cursor if cursor else (None, None)
        return _rows(
            await self.conn.fetch(
                _PLAN_SELECT + "WHERE v.tenant_id = $1 AND ($2::text IS NULL OR v.vin = $2) "
                "AND ($3::text IS NULL OR p.status = $3) "
                "AND ($4::timestamptz IS NULL OR (p.created_ts, p.id) < ($4, $5::bigint)) "
                "ORDER BY p.created_ts DESC, p.id DESC LIMIT $6",
                self.tenant_id,
                vin,
                status,
                c_ts,
                c_id,
                limit,
            )
        )


class PgAlertRepository(_Base):
    async def find(
        self,
        *,
        severity: str | None,
        since: datetime | None,
        acked: bool | None,
        cursor: tuple[datetime, int] | None,
        limit: int,
    ) -> list[Row]:
        c_ts, c_id = cursor if cursor else (None, None)
        return _rows(
            await self.conn.fetch(
                "SELECT a.id, v.vin, a.type, a.severity, a.ts, a.payload, a.acked_by, a.acked_ts "
                "FROM alert a JOIN vehicle v ON v.id = a.vehicle_id WHERE a.tenant_id = $1 "
                "AND ($2::text IS NULL OR a.severity = $2) AND ($3::timestamptz IS NULL OR a.ts >= $3) "
                "AND ($4::boolean IS NULL OR (a.acked_ts IS NOT NULL) = $4) "
                "AND ($5::timestamptz IS NULL OR (a.ts, a.id) < ($5, $6::bigint)) "
                "ORDER BY a.ts DESC, a.id DESC LIMIT $7",
                self.tenant_id,
                severity,
                since,
                acked,
                c_ts,
                c_id,
                limit,
            )
        )

    async def ack(self, alert_id: int, acked_by: str) -> Row | None:
        r = await self.conn.fetchrow(
            "UPDATE alert SET acked_by = $3, acked_ts = now() "
            "WHERE tenant_id = $1 AND id = $2 AND acked_ts IS NULL "
            "RETURNING id, acked_by, acked_ts",
            self.tenant_id,
            alert_id,
            acked_by,
        )
        if r is None:  # already acked (idempotent) or unknown
            r = await self.conn.fetchrow(
                "SELECT id, acked_by, acked_ts FROM alert WHERE tenant_id = $1 AND id = $2",
                self.tenant_id,
                alert_id,
            )
        return dict(r) if r else None

    async def count_open(self) -> int:
        return int(
            await self.conn.fetchval(
                "SELECT count(*) FROM alert WHERE tenant_id = $1 AND acked_ts IS NULL", self.tenant_id
            )
        )


class PgChargerRepository(_Base):
    async def find(
        self, *, depot_id: int | None, public: bool | None, after_id: int | None, limit: int
    ) -> list[Row]:
        return _rows(
            await self.conn.fetch(
                "SELECT id, tenant_id, depot_id, name, lat, lon, max_kw, connector, is_public, price_per_kwh "
                "FROM charger WHERE (tenant_id = $1 OR is_public) AND ($2::int IS NULL OR depot_id = $2) "
                "AND ($3::boolean IS NULL OR is_public = $3) AND ($4::int IS NULL OR id > $4) "
                "ORDER BY id LIMIT $5",
                self.tenant_id,
                depot_id,
                public,
                after_id,
                limit,
            )
        )


async def all_chargers(conn: asyncpg.Connection) -> list[Row]:
    """Startup helper (admin pool): every charger, for the Redis geo index."""
    return _rows(
        await conn.fetch(
            "SELECT id, tenant_id, depot_id, name, lat, lon, max_kw, connector, is_public, price_per_kwh "
            "FROM charger"
        )
    )


class PgTariffRepository(_Base):
    async def current(self) -> Row | None:
        t = await self.conn.fetchrow(
            "SELECT id, name, timezone FROM tariff WHERE tenant_id = $1 ORDER BY id LIMIT 1", self.tenant_id
        )
        if t is None:
            return None
        slots = await self.conn.fetch(
            "SELECT dow, start_min, end_min, price_per_kwh FROM tariff_slot WHERE tariff_id = $1 "
            "ORDER BY dow, start_min",
            t["id"],
        )
        return {"tariff_id": t["id"], "name": t["name"], "timezone": t["timezone"], "slots": _rows(slots)}


HNSW_ITERATIVE_SCAN = "SET LOCAL hnsw.iterative_scan = strict_order"  # pgvector >= 0.8
BATTERY_HEALTH_SQL = """
SELECT v.vin, l.soh_pct, l.cycles,
  l.soh_pct - (SELECT b2.soh_pct FROM battery_health b2 WHERE b2.vehicle_id = l.vehicle_id
               AND b2.computed_ts <= l.computed_ts - interval '30 days'
               ORDER BY b2.computed_ts DESC LIMIT 1) AS trend_30d,
  CASE WHEN l.embedding IS NULL THEN ARRAY[]::text[] ELSE COALESCE((
    SELECT array_agg(v2.vin ORDER BY n.dist) FROM (
      SELECT b3.vehicle_id, b3.embedding <-> l.embedding AS dist
      FROM battery_health b3
      WHERE b3.tenant_id = $1 AND b3.embedding IS NOT NULL AND b3.vehicle_id <> l.vehicle_id
      ORDER BY b3.embedding <-> l.embedding LIMIT 5) n
    JOIN vehicle v2 ON v2.id = n.vehicle_id AND v2.tenant_id = $1), ARRAY[]::text[]) END AS similar
FROM vehicle v
CROSS JOIN LATERAL (
  SELECT b.vehicle_id, b.soh_pct, b.cycles, b.computed_ts, b.embedding
  FROM battery_health b WHERE b.vehicle_id = v.id ORDER BY b.computed_ts DESC LIMIT 1) l
WHERE v.tenant_id = $1 AND ($2::text IS NULL OR v.vin > $2)
ORDER BY v.vin LIMIT $3
"""


class PgReportRepository(_Base):
    async def cost_by_day(self, from_ts: datetime, to_ts: datetime) -> list[Row]:
        return _rows(
            await self.conn.fetch(
                "SELECT to_char(date_trunc('day', s.start_ts), 'YYYY-MM-DD') AS date, "
                "COALESCE(sum(s.kwh), 0) AS kwh, "
                "COALESCE(sum(COALESCE(s.cost, s.kwh * $4)), 0) AS cost, "
                "COALESCE(sum(s.kwh * $4), 0) AS baseline_cost, "
                "COALESCE(max(CASE WHEN s.end_ts > s.start_ts THEN s.kwh / "
                "  (EXTRACT(EPOCH FROM (s.end_ts - s.start_ts)) / 3600.0) END), 0) AS peak_kw "
                "FROM charging_session s JOIN vehicle v ON v.id = s.vehicle_id "
                "WHERE v.tenant_id = $1 AND s.start_ts >= $2 AND s.start_ts < $3 "
                "GROUP BY 1 ORDER BY 1",
                self.tenant_id,
                from_ts,
                to_ts,
                PEAK_PRICE_PER_KWH,
            )
        )

    async def battery_health(self, *, after_vin: str | None, limit: int) -> list[Row]:
        # Latest reading per vehicle (keyset on vin, one index probe per vehicle), 30-day trend, and the
        # 5 nearest degradation profiles via the HNSW index (db/migrations/0004_ml.sql). The KNN subquery
        # filters battery_health.tenant_id directly so the index scan stays tenant-scoped without a join;
        # iterative scan keeps walking the graph until 5 same-tenant neighbours are found.
        await self.conn.execute(HNSW_ITERATIVE_SCAN)
        return _rows(await self.conn.fetch(BATTERY_HEALTH_SQL, self.tenant_id, after_vin, limit))


class PgAgentRepository(_Base):
    async def get(self, action_id: int) -> Row | None:
        r = await self.conn.fetchrow(
            "SELECT id, action, params, rationale, status, decided_by, decided_ts FROM agent_action "
            "WHERE tenant_id = $1 AND id = $2",
            self.tenant_id,
            action_id,
        )
        return dict(r) if r else None

    async def propose(self, *, action: str, params: dict[str, Any], rationale: str, proposed_by: str) -> Row:
        r = await self.conn.fetchrow(
            "INSERT INTO agent_action (tenant_id, proposed_by, action, params, rationale, status) "
            "VALUES ($1, $2, $3, $4, $5, 'PENDING') "
            "RETURNING id, action, params, rationale, status, created_ts",
            self.tenant_id,
            proposed_by,
            action,
            params,
            rationale,
        )
        return dict(r)

    async def set_status(self, action_id: int, *, status: str, from_status: str) -> Row | None:
        r = await self.conn.fetchrow(
            "UPDATE agent_action SET status = $3 WHERE tenant_id = $1 AND id = $2 AND status = $4 "
            "RETURNING id, status",
            self.tenant_id,
            action_id,
            status,
            from_status,
        )
        return dict(r) if r else None

    async def decide(self, action_id: int, *, status: str, decided_by: str) -> Row | None:
        r = await self.conn.fetchrow(
            "UPDATE agent_action SET status = $3, decided_by = $4, decided_ts = now() "
            "WHERE tenant_id = $1 AND id = $2 AND status = 'PENDING' RETURNING id, status",
            self.tenant_id,
            action_id,
            status,
            decided_by,
        )
        return dict(r) if r else None


class PgPrivacyRepository(_Base):
    async def erase_driver(self, driver_id: int) -> bool:
        r = await self.conn.fetchrow(
            "UPDATE driver SET name_masked = 'ERASED', licence_hash = '', erased_at = now() "
            "WHERE tenant_id = $1 AND id = $2 RETURNING id",
            self.tenant_id,
            driver_id,
        )
        if r is None:
            return False
        await self.conn.execute("DELETE FROM driver_assignment WHERE driver_id = $1", driver_id)
        return True


class PgAuditRepository(_Base):
    async def insert(
        self, *, actor_id: str, action: str, resource: str, details: dict[str, Any], actor_type: str = "user"
    ) -> int:
        return int(
            await self.conn.fetchval(
                "INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, resource, details) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                self.tenant_id,
                actor_type,
                actor_id,
                action,
                resource,
                details,
            )
        )

    async def find(self, *, cursor: tuple[datetime, int] | None, limit: int) -> list[Row]:
        c_ts, c_id = cursor if cursor else (None, None)
        return _rows(
            await self.conn.fetch(
                "SELECT id, tenant_id, actor_type, actor_id, action, resource, ts, details FROM audit_log "
                "WHERE tenant_id = $1 AND ($2::timestamptz IS NULL OR (ts, id) < ($2, $3::bigint)) "
                "ORDER BY ts DESC, id DESC LIMIT $4",
                self.tenant_id,
                c_ts,
                c_id,
                limit,
            )
        )


def pg_repos(conn: asyncpg.Connection, tenant_id: int) -> Repos:
    return Repos(
        tenant_id=tenant_id,
        fleets=PgFleetRepository(conn, tenant_id),
        vehicles=PgVehicleRepository(conn, tenant_id),
        plans=PgPlanRepository(conn, tenant_id),
        alerts=PgAlertRepository(conn, tenant_id),
        chargers=PgChargerRepository(conn, tenant_id),
        tariffs=PgTariffRepository(conn, tenant_id),
        reports=PgReportRepository(conn, tenant_id),
        agent=PgAgentRepository(conn, tenant_id),
        privacy=PgPrivacyRepository(conn, tenant_id),
        audit=PgAuditRepository(conn, tenant_id),
    )
