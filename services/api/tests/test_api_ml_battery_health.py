"""/reports/battery-health: `similar` = pgvector nearest neighbours, same tenant, never the vehicle itself."""

from __future__ import annotations

from api_fakes import VIN_T1, VIN_T2
from orbit_api.repositories.postgres import BATTERY_HEALTH_SQL, HNSW_ITERATIVE_SCAN, pg_repos
from test_api_repositories_pg import StubConn


def _row(tenant: int, vin: str, emb: list[float], soh: float = 90.0) -> dict:
    return {
        "tenant_id": tenant,
        "vin": vin,
        "soh_pct": soh,
        "cycles": 100,
        "trend_30d": None,
        "embedding": emb,
    }


async def test_similar_is_tenant_scoped_nearest_neighbours(client, data, headers):
    extra = [f"WVWZZZ1JZXW{n:06d}" for n in range(6)]
    data.battery = [
        _row(1, VIN_T1[0], [1.0, 0.0], soh=78.0),
        _row(1, VIN_T1[1], [0.9, 0.1]),
        _row(1, VIN_T1[2], [0.0, 1.0]),
        *[_row(1, v, [0.5 + i / 10, 0.5]) for i, v in enumerate(extra)],
        _row(2, VIN_T2[0], [1.0, 0.0]),  # identical vector, other tenant: must never appear
    ]
    r = await client.get("/api/v1/reports/battery-health?limit=50", headers=headers)
    assert r.status_code == 200
    items = {i["vin"]: i for i in r.json()["items"]}
    assert VIN_T2[0] not in items
    sim = items[VIN_T1[0]]["similar"]
    assert len(sim) == 5 and sim[0] == VIN_T1[1] and VIN_T1[0] not in sim and VIN_T2[0] not in sim
    assert all(v in items for v in sim)
    assert items[VIN_T1[0]]["soh_pct"] == 78.0 and "embedding" not in items[VIN_T1[0]]


def test_battery_health_sql_uses_index_and_tenant():
    sql = " ".join(BATTERY_HEALTH_SQL.split())
    knn = sql[sql.index("FROM battery_health b3") : sql.index("LIMIT 5")]
    # ORDER BY the distance operator directly on the indexed column, filtered on the denormalised tenant_id
    assert "b3.tenant_id = $1" in knn and "ORDER BY b3.embedding <-> l.embedding" in knn
    assert "b3.vehicle_id <> l.vehicle_id" in knn and "JOIN" not in knn
    assert "v.tenant_id = $1" in sql and "v2.tenant_id = $1" in sql
    assert "iterative_scan" in HNSW_ITERATIVE_SCAN


async def test_battery_health_repository_sets_iterative_scan():
    conn = StubConn(rows=[{"vin": "X", "soh_pct": 90.0, "cycles": 1, "trend_30d": None, "similar": ["Y"]}])
    rows = await pg_repos(conn, 3).reports.battery_health(after_vin=None, limit=10)
    assert rows[0]["similar"] == ["Y"]
    assert conn.calls[0][0] == HNSW_ITERATIVE_SCAN
    assert conn.calls[1][1] == (3, None, 10)
