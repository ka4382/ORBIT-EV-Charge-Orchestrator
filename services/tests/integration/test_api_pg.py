"""Integration: real TimescaleDB (Testcontainers) + db/migrations + the API's RLS-scoped connection path.

Proves that a tenant-1 token cannot see tenant-2's vehicles or alerts, both through the HTTP API and directly
at the Postgres level (orbit_app role + `SET LOCAL app.tenant_id`). Run: `make test-integration` or
`cd services && uv run pytest -m integration tests/integration`.
"""

from __future__ import annotations

import importlib
import os
import pathlib
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

pytestmark = pytest.mark.integration

REPO = pathlib.Path(__file__).resolve().parents[3]
MIGRATIONS = REPO / "db" / "migrations"
IMAGE = "timescale/timescaledb-ha:pg16"
PG_PASSWORD = "postgres"  # noqa: S105 - throwaway container credential
VINS_T1 = ["1HGCM82633A004352", "5YJ3E1EA7KF317000", "WBA3A5C50CF256789"]
VINS_T2 = ["JH4KA7561PC008269", "2T1BURHE0FC311111", "KMHDU4AD8AU765432"]


def split_sql(text: str) -> list[str]:
    """Split a migration into statements, honouring $$ dollar-quoted bodies (DO blocks, functions).
    Timescale's continuous-aggregate DDL refuses to run inside a multi-statement string, hence one by one."""
    statements: list[str] = []
    buf: list[str] = []
    in_dollar = False
    i = 0
    while i < len(text):
        if text.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        ch = text[i]
        if ch == "-" and text.startswith("--", i) and not in_dollar:
            end = text.find("\n", i)
            i = len(text) if end == -1 else end
            continue
        if ch == ";" and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


async def apply_migrations(admin_url: str) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        for f in sorted(MIGRATIONS.glob("*.sql")):
            for stmt in split_sql(f.read_text()):
                await conn.execute(stmt)
    finally:
        await conn.close()


async def seed(admin_url: str) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(
            "INSERT INTO oem(code, name, payload_format) VALUES ('OEM_A', 'Alpha', 'flat_iso')"
        )
        await conn.execute(
            "INSERT INTO vehicle_model(oem_id, name, powertrain, battery_capacity_kwh, nominal_range_km) "
            "VALUES (1, 'A-Van 60', 'BEV', 60, 320)"
        )
        for t, vins in ((1, VINS_T1), (2, VINS_T2)):
            await conn.execute("INSERT INTO tenant(name) VALUES ($1)", f"Fleet Co {t}")
            await conn.execute(
                "INSERT INTO depot(tenant_id, name, lat, lon, grid_capacity_kw) "
                "VALUES ($1, 'Depot', 13.08, 80.27, 600)",
                t,
            )
            await conn.execute("INSERT INTO fleet(tenant_id, depot_id, name) VALUES ($1, $1, 'Fleet 1')", t)
            for i, vin in enumerate(vins):
                vid = await conn.fetchval(
                    "INSERT INTO vehicle(vin, tenant_id, fleet_id, model_id, plate) "
                    "VALUES ($1, $2, $3, 1, $4) RETURNING id",
                    vin,
                    t,
                    t,
                    f"T{t}-{i}",
                )
                await conn.execute(
                    "INSERT INTO alert(tenant_id, vehicle_id, type, severity, ts, payload) "
                    "VALUES ($1, $2, 'LOW_SOC', 'WARN', now(), '{\"soc\": 12}')",
                    t,
                    vid,
                )
            await conn.execute(
                "INSERT INTO driver(tenant_id, name_masked, licence_hash) VALUES ($1, 'R*** S***', 'h')", t
            )
    finally:
        await conn.close()


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish_plan_requested(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def ready(self) -> bool:
        return True


@pytest.fixture(scope="module")
def pg_urls() -> Any:
    postgres = importlib.import_module("testcontainers.postgres")
    container = postgres.PostgresContainer(
        IMAGE, username="postgres", password=PG_PASSWORD, dbname="orbit", driver=None
    )
    container.with_command("postgres -c shared_preload_libraries=timescaledb -c max_connections=100")
    with container:
        host, port = container.get_container_host_ip(), container.get_exposed_port(5432)
        admin = f"postgresql://postgres:{PG_PASSWORD}@{host}:{port}/orbit"
        app = f"postgresql://orbit_app:orbit_app@{host}:{port}/orbit"
        yield admin, app


@pytest.fixture(scope="module")
def prepared_db(pg_urls: tuple[str, str]) -> tuple[str, str]:
    import asyncio

    admin, app = pg_urls
    asyncio.run(apply_migrations(admin))
    asyncio.run(seed(admin))
    return admin, app


@pytest.fixture
async def api(prepared_db: tuple[str, str]) -> AsyncIterator[Any]:
    os.environ.setdefault("ORBIT_ENV", "dev")
    from httpx import ASGITransport, AsyncClient
    from orbit_api.audit import AuditQueue, pg_sink_factory
    from orbit_api.db import Database
    from orbit_api.main import Resources, create_app
    from orbit_api.ratelimit import RateLimiter

    admin, app_url = prepared_db
    fakeredis = importlib.import_module("fakeredis.aioredis")
    redis = fakeredis.FakeRedis(decode_responses=True)
    db = await Database.connect(app_url, admin, min_size=1, max_size=4)
    publisher = RecordingPublisher()
    res = Resources(
        db=db,
        redis=redis,
        publisher=publisher,
        audit=AuditQueue(await pg_sink_factory(db.admin_pool)),
        rate_limiter=RateLimiter(redis, capacity=10_000),
        preset=True,
    )
    app = create_app(res)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        client.publisher = publisher  # type: ignore[attr-defined]
        client.audit = res.audit  # type: ignore[attr-defined]
        yield client
    await db.close()


def bearer(tenant_id: int, role: str = "manager", sub: str = "it-user") -> dict[str, str]:
    from orbit_api.auth import mint_dev_token

    return {"Authorization": f"Bearer {mint_dev_token(tenant_id, role, sub)}"}


async def test_rls_isolates_vehicles_and_alerts_over_http(api: Any) -> None:
    r1 = await api.get("/api/v1/vehicles", headers=bearer(1))
    assert r1.status_code == 200, r1.text
    assert sorted(v["vin"] for v in r1.json()["items"]) == sorted(VINS_T1)
    r2 = await api.get("/api/v1/vehicles", headers=bearer(2))
    assert sorted(v["vin"] for v in r2.json()["items"]) == sorted(VINS_T2)

    # direct lookups across the boundary are 404, never 403 (no existence leak)
    assert (await api.get(f"/api/v1/vehicles/{VINS_T2[0]}", headers=bearer(1))).status_code == 404
    assert (await api.get(f"/api/v1/vehicles/{VINS_T1[0]}", headers=bearer(2))).status_code == 404
    assert (await api.get(f"/api/v1/vehicles/{VINS_T1[0]}", headers=bearer(1))).status_code == 200

    a1 = (await api.get("/api/v1/alerts", headers=bearer(1))).json()["items"]
    a2 = (await api.get("/api/v1/alerts", headers=bearer(2))).json()["items"]
    assert len(a1) == 3 and {a["vin"] for a in a1} == set(VINS_T1)
    assert len(a2) == 3 and {a["vin"] for a in a2} == set(VINS_T2)
    assert {a["id"] for a in a1}.isdisjoint({a["id"] for a in a2})
    # acking another tenant's alert is a 404
    assert (await api.post(f"/api/v1/alerts/{a2[0]['id']}/ack", headers=bearer(1))).status_code == 404

    fleets = (await api.get("/api/v1/fleets", headers=bearer(1))).json()["items"]
    assert fleets == [{"id": 1, "name": "Fleet 1", "depot_id": 1, "vehicle_count": 3}]
    summary = (await api.get("/api/v1/fleet/summary", headers=bearer(2))).json()
    assert summary["vehicles"] == 3 and summary["open_alerts"] == 3


async def test_rls_enforced_at_database_level(prepared_db: tuple[str, str]) -> None:
    """Even a query with no tenant filter only sees the scoped tenant when connected as orbit_app."""
    _, app_url = prepared_db
    conn = await asyncpg.connect(app_url)
    try:
        assert await conn.fetchval("SELECT count(*) FROM vehicle") == 0  # no tenant set -> nothing visible
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', '1', true)")
            vins = {r["vin"] for r in await conn.fetch("SELECT vin FROM vehicle")}
            assert vins == set(VINS_T1)
            assert await conn.fetchval("SELECT count(*) FROM alert") == 3
            # WITH CHECK: cannot smuggle a row into another tenant
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "INSERT INTO alert(tenant_id, vehicle_id, type, severity, ts) "
                    "VALUES (2, 4, 'X', 'INFO', now())"
                )
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', '2', true)")
            assert {r["vin"] for r in await conn.fetch("SELECT vin FROM vehicle")} == set(VINS_T2)
        assert await conn.fetchval("SELECT count(*) FROM vehicle") == 0  # SET LOCAL did not leak
    finally:
        await conn.close()


async def test_plan_write_and_audit_trail(api: Any, prepared_db: tuple[str, str]) -> None:
    body = {"vin": VINS_T1[0], "target_soc": 80, "depart_ts": "2030-01-01T06:00:00Z"}
    r = await api.post("/api/v1/plans", headers=bearer(1, sub="planner-1"), json=body)
    assert r.status_code == 202, r.text
    plan_id = r.json()["plan_id"]
    assert api.publisher.events[-1]["plan_id"] == plan_id and api.publisher.events[-1]["tenant_id"] == 1
    detail = (await api.get(f"/api/v1/plans/{plan_id}", headers=bearer(1))).json()
    assert detail["status"] == "PENDING" and detail["vin"] == VINS_T1[0] and detail["slots"] == []
    assert (await api.get(f"/api/v1/plans/{plan_id}", headers=bearer(2))).status_code == 404

    # right to erasure, then the audit log (domain row + async access row) as seen by an auditor
    r = await api.delete("/api/v1/privacy/drivers/1", headers=bearer(1, role="admin", sub="dpo"))
    assert r.status_code == 202
    await api.audit.flush()
    admin_url, _ = prepared_db
    conn = await asyncpg.connect(admin_url)
    try:
        assert await conn.fetchval("SELECT name_masked FROM driver WHERE id = 1") == "ERASED"
        actions = {r["action"] for r in await conn.fetch("SELECT action FROM audit_log WHERE tenant_id = 1")}
    finally:
        await conn.close()
    assert {
        "PLAN_REQUESTED",
        "ERASURE",
        "POST /api/v1/plans",
        "DELETE /api/v1/privacy/drivers/{driver_id}",
    } <= actions
    rows = (await api.get("/api/v1/audit", headers=bearer(1, role="auditor"))).json()["items"]
    assert all(row["tenant_id"] == 1 for row in rows) and any(row["action"] == "ERASURE" for row in rows)
    assert (await api.get("/api/v1/audit", headers=bearer(2, role="auditor"))).json()["items"] == []
