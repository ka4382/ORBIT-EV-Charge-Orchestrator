from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from ml_fakes import StubConn
from orbit_ml.data import Frame, synthetic_fleet
from orbit_ml.embeddings import (
    DIM,
    EMBED_FEATURES,
    WEIGHTS,
    Standardiser,
    add_model_columns,
    battery_health_rows,
    embed,
    require_schema,
    vector_literal,
    write_battery_health,
)

TS = datetime(2026, 10, 1, tzinfo=UTC)


def _frame(n: int = 400, seed: int = 3) -> Frame:
    f = synthetic_fleet(n_vehicles=n, snapshots=1, seed=seed, fast_share=0.1)
    return add_model_columns(f, f["y_soh_30d"], loss_per_km=1e-5)


def test_embedding_shape_and_unit_norm():
    f = _frame()
    v, scaler = embed(f)
    assert v.shape == (len(f), DIM) == (400, 32) and v.dtype == np.float32
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5)
    assert len(EMBED_FEATURES) == len(WEIGHTS) == DIM
    v2, _ = embed(f, scaler)  # same scaler -> same vectors
    assert np.allclose(v, v2)


def test_embedding_handles_missing_and_constant_columns():
    f = _frame(50)
    f.cols["soh_slope_per_100kwh"] = np.full(50, np.nan)  # nobody charged in the window
    f.cols["battery_kwh"] = np.full(50, 60.0)
    v, s = embed(f)
    assert np.isfinite(v).all() and np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5)
    zero = Frame({k: np.full(1, np.nan) for k in EMBED_FEATURES})
    vz, _ = embed(zero)  # all-unknown vehicle gets a fixed unit vector, not NaN
    assert np.allclose(np.linalg.norm(vz), 1.0)


def test_degrading_vehicles_are_nearest_to_each_other():
    f = _frame(600, seed=8)
    v, _ = embed(f)
    fast = np.asarray(f["fast_degrade"], bool)
    assert fast.sum() > 20
    hits = []
    for i in np.flatnonzero(fast)[:30]:
        d = np.linalg.norm(v - v[i], axis=1)
        d[i] = np.inf
        hits.append(fast[np.argsort(d)[:5]].mean())
    # the cohort is 10 % of the fleet; neighbours of a fast degrader are mostly fast degraders
    assert np.mean(hits) > 0.5


def test_standardiser():
    x = np.array([[1.0, np.nan], [3.0, np.nan], [100.0, np.nan]])
    s = Standardiser.fit(x)
    z = s.transform(x)
    assert z[:, 1].tolist() == [0.0, 0.0, 0.0] and np.isfinite(z).all() and np.abs(z).max() <= 5.0


def test_vector_literal():
    assert vector_literal([1, 0.5, -0.25]) == "[1.000000,0.500000,-0.250000]"


def test_battery_health_rows_and_write():
    f = _frame(5)
    f.cols["vehicle_id"] = np.arange(10, 15)
    f.cols["tenant_id"] = np.array([1, 1, 2, 2, 2])
    f.cols["soh_pct"] = np.array([95.0, np.nan, 120.0, 80.0, 70.0])
    f.cols["cycles"] = np.array([10.4, 1.0, np.nan, 3.0, 4.0])
    v, _ = embed(f)
    soh30 = np.array([94.0, 1.0, np.nan, 79.0, -3.0])
    rows = battery_health_rows(f, v, soh30, TS, "model_v1")
    assert [r[0] for r in rows] == [10, 12, 13, 14]  # no SoH reading -> no row
    first = rows[0]
    assert first[1] == 1 and first[2] == TS and first[3] == 95.0 and first[4] == 10 and first[5] == "model_v1"
    assert first[6].startswith("[") and first[6].count(",") == 31 and first[7] == 94.0
    assert rows[1][3] == 100.0 and rows[1][4] is None and rows[1][7] is None  # clamped / unknown
    assert rows[3][7] == 0.0
    conn = StubConn()
    assert write_battery_health(conn, rows, TS, batch=2) == 4
    assert len(conn.inserted) == 4 and "SET embedding = NULL" in conn.statements[0]
    assert conn.params[0]["ids"] == [10, 12, 13, 14] and conn.params[0]["ts"] == TS
    assert sum("INSERT INTO battery_health" in s for s in conn.statements) == 2
    assert write_battery_health(StubConn(), [], TS) == 0


def test_require_schema():
    require_schema(StubConn())
    with pytest.raises(RuntimeError, match="0004_ml.sql"):
        require_schema(StubConn(columns=("tenant_id",)))


def test_migration_declares_hnsw_index():
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[3] / "db" / "migrations" / "0004_ml.sql").read_text()
    assert "USING hnsw (embedding vector_l2_ops)" in sql and "CREATE INDEX IF NOT EXISTS" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql and "tenant_id" in sql
