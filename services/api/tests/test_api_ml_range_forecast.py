"""/vehicles/{vin}/range-forecast: hgb_v1 coefficients from Redis when published, baseline_v0 otherwise."""

from __future__ import annotations

import orjson
from api_fakes import VIN_T1, VIN_T2
from orbit_api.routers.vehicles import ml_confidence
from test_api_vehicles import put_state


async def put_coeffs(redis, vin: str, **doc) -> None:
    body = {
        "km_per_soc_pct": 3.0,
        "method": "hgb_v1",
        "mae_km": 6.0,
        "trained_at": "2026-10-01T00:00:00+00:00",
    }
    body.update(doc)
    await redis.set(f"range:{vin}", orjson.dumps(body), ex=86400)


def test_ml_confidence():
    assert ml_confidence(150.0, 6.0) == 0.95  # capped
    assert ml_confidence(20.0, 6.0) == 0.7
    assert ml_confidence(0.5, 6.0) == 0.0  # error larger than the range itself
    assert ml_confidence(100.0, 0.0) == 0.95


async def test_range_forecast_uses_published_coefficients(client, redis, headers):
    await put_state(redis, VIN_T1[0], soc_pct=40.0, ambient_c=-5)  # temperature already inside the model
    await put_coeffs(redis, VIN_T1[0])
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[0]}/range-forecast", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"range_km": 120.0, "method": "hgb_v1", "confidence": 0.95}
    await put_state(redis, VIN_T1[0], soc_pct=5.0)
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[0]}/range-forecast", headers=headers)
    assert r.json() == {"range_km": 15.0, "method": "hgb_v1", "confidence": 0.6}


async def test_range_forecast_falls_back_to_baseline(client, redis, headers):
    await put_state(redis, VIN_T1[1], soc_pct=25.0)
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[1]}/range-forecast", headers=headers)
    assert r.json() == {"range_km": 80.0, "method": "baseline_v0", "confidence": 0.5}
    for bad in (b"not json", orjson.dumps({"method": "hgb_v1"}), orjson.dumps({"km_per_soc_pct": -1})):
        await redis.set(f"range:{VIN_T1[1]}", bad)
        r = await client.get(f"/api/v1/vehicles/{VIN_T1[1]}/range-forecast", headers=headers)
        assert r.json()["method"] == "baseline_v0", bad
    await put_coeffs(redis, VIN_T1[1], mae_km=float("nan"))
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[1]}/range-forecast", headers=headers)
    assert r.json()["method"] == "baseline_v0"


async def test_range_forecast_needs_soc_and_tenant(client, redis, headers):
    await put_coeffs(redis, VIN_T1[2])  # coefficients alone are not enough: no live SoC
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[2]}/range-forecast", headers=headers)
    assert r.status_code == 404
    await put_coeffs(redis, VIN_T2[0])
    await put_state(redis, VIN_T2[0], soc_pct=50.0)
    r = await client.get(
        f"/api/v1/vehicles/{VIN_T2[0]}/range-forecast", headers=headers
    )  # tenant 2's vehicle
    assert r.status_code == 404
