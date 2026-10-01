from __future__ import annotations

import json

import fakeredis
import numpy as np
import pytest
from ml_fakes import StubConn, agg_row
from orbit_ml import models as m
from orbit_ml.data import Frame, features_from_rows, synthetic_fleet
from orbit_ml.publish import RANGE_TTL_S, publish, publish_range, range_payload


@pytest.fixture(scope="module")
def trained() -> m.TrainedModels:
    return m.train(synthetic_fleet(n_vehicles=800, seed=2), seed=2)


def test_range_payload_format():
    doc = json.loads(range_payload(1.234567, 3.14159, "2026-10-01T00:00:00+00:00"))
    assert doc == {
        "km_per_soc_pct": 1.2346,
        "method": "hgb_v1",
        "mae_km": 3.14,
        "trained_at": "2026-10-01T00:00:00+00:00",
    }


def test_publish_range_sets_keys_with_ttl():
    r = fakeredis.FakeRedis(decode_responses=True)
    n = publish_range(
        r, ["V1", "V2", "V3", "V4"], np.array([3.1, np.nan, 0.0, 2.2]), mae_km=4.0, trained_at="t", batch=1
    )
    assert n == 2
    assert sorted(r.keys("range:*")) == ["range:V1", "range:V4"]
    doc = json.loads(r.get("range:V1"))
    assert doc["km_per_soc_pct"] == 3.1 and doc["method"] == "hgb_v1" and doc["mae_km"] == 4.0
    assert 0 < r.ttl("range:V1") <= RANGE_TTL_S


def test_publish_end_to_end(trained):
    rows = [agg_row(f"VIN{i:014d}", i + 1, tenant_id=1 + i % 2, soh_pct=95.0 - i) for i in range(6)]
    live = features_from_rows(rows, [])
    conn, r = StubConn(), fakeredis.FakeRedis(decode_responses=True)
    res = publish(trained, live, conn, r)
    assert res.vehicles == 6 and res.battery_health_rows == 6 and res.range_keys == 6
    assert conn.transactions == 1 and len(conn.inserted) == 6
    assert {row[1] for row in conn.inserted} == {1, 2}  # tenant carried on every row
    doc = json.loads(r.get("range:VIN00000000000000"))
    assert 320 / 100 * 0.6 <= doc["km_per_soc_pct"] <= 320 / 100 * 1.3
    assert doc["mae_km"] == round(trained.range_mae_km, 2)


def test_publish_empty_and_schema_guard(trained):
    assert publish(trained, Frame(), StubConn(), fakeredis.FakeRedis()).vehicles == 0
    live = features_from_rows([agg_row("VIN00000000000001", 1)], [])
    with pytest.raises(RuntimeError):
        publish(trained, live, StubConn(columns=()), fakeredis.FakeRedis())
