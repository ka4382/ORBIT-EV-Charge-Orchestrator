from __future__ import annotations

import orjson
from api_auth import auth
from api_fakes import VIN_T1
from orbit_api.masking import mask_location, masks_location
from orbit_api.routers.vehicles import remaining_range_km, temp_factor
from orbit_api.state import parse_state, parse_summary


async def put_state(redis, vin: str, **over):
    fields = {
        "vin": vin,
        "tenant_id": "1",
        "ts": "2026-10-01T12:00:00Z",
        "seq": "88412",
        "lat": "13.082712",
        "lon": "80.270734",
        "speed_kmh": "64.2",
        "soc_pct": "41.0",
        "soh_pct": "93.5",
        "odo_km": "18234.7",
        "charging_state": "NONE",
        "charging_kw": "0",
        "dtc": orjson.dumps(["P0301"]).decode(),
        "status": "DRIVING",
    }
    fields.update({k: str(v) for k, v in over.items()})
    # contracts/redis.md: veh:{vin} is one JSON document written with SET ... EX by the state service
    doc = {k: (orjson.loads(v) if k == "dtc" else v) for k, v in fields.items()}
    await redis.set(f"veh:{vin}", orjson.dumps(doc), ex=600)


def test_masking_helpers():
    assert masks_location("finance") and masks_location("auditor") and not masks_location("manager")
    assert mask_location("finance", {"lat": 13.082712, "lon": 80.270734, "x": 1}) == {
        "lat": 13.08,
        "lon": 80.27,
        "x": 1,
    }
    assert mask_location("admin", {"lat": 13.082712}) == {"lat": 13.082712}
    assert mask_location("finance", None) is None
    assert mask_location("finance", {"lat": None}) == {"lat": None}


def test_parse_state_and_summary():
    assert parse_state({}) is None and parse_state(None) is None
    st = parse_state({"vin": "X", "seq": "5", "lat": "1.5", "dtc": "P0301,P0302", "soc_pct": "bad"})
    assert st["seq"] == 5 and st["lat"] == 1.5 and st["dtc"] == ["P0301", "P0302"] and st["soc_pct"] is None
    assert st["status"] == "OFFLINE" and st["charging_state"] == "NONE"
    assert parse_state({"vin": "X"})["dtc"] == []
    assert parse_state({"vin": "X", "dtc": '"str"'})["dtc"] == []
    assert parse_summary({}) is None
    assert parse_summary({"vehicles": "3", "avg_soc": "55.5"})["avg_soc"] == 55.5


def test_range_baseline():
    assert temp_factor(None) == 1.0 and temp_factor(-5) == 0.7 and temp_factor(5) == 0.85
    assert temp_factor(40) == 0.9 and temp_factor(25) == 1.0
    v = {"nominal_range_km": 320}
    assert remaining_range_km(v, {"soc_pct": 50}) == 160.0
    assert remaining_range_km(v, {"soc_pct": 50, "ambient_c": -3}) == 112.0
    assert remaining_range_km(v, None) is None and remaining_range_km({}, {"soc_pct": 50}) is None


async def test_list_vehicles_joins_state(client, redis, headers):
    await put_state(redis, VIN_T1[0])
    r = await client.get("/api/v1/vehicles", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 3
    assert items[0]["state"]["soc_pct"] == 41.0 and items[0]["state"]["dtc"] == ["P0301"]
    assert items[0]["state"]["lat"] == 13.082712
    assert items[1]["state"] is None
    assert {"id", "vin", "plate", "fleet_id", "model", "powertrain", "state"} <= set(items[0])


async def test_list_vehicles_filters(client, redis, headers):
    await put_state(redis, VIN_T1[0], status="CHARGING")
    r = await client.get("/api/v1/vehicles?status=charging", headers=headers)
    assert [v["vin"] for v in r.json()["items"]] == [VIN_T1[0]]
    r = await client.get("/api/v1/vehicles?status=OFFLINE", headers=headers)
    assert [v["vin"] for v in r.json()["items"]] == VIN_T1[1:]
    r = await client.get("/api/v1/vehicles?fleet_id=2", headers=headers)
    assert r.json()["items"] == []
    assert (await client.get("/api/v1/vehicles?status=FLYING", headers=headers)).status_code == 422


async def test_location_masking_by_role(client, redis):
    await put_state(redis, VIN_T1[0])
    for role in ("finance", "auditor"):
        r = await client.get(f"/api/v1/vehicles/{VIN_T1[0]}/state", headers=auth(role=role))
        assert r.status_code == 200
        assert r.json()["lat"] == 13.08 and r.json()["lon"] == 80.27
        r = await client.get(f"/api/v1/vehicles/{VIN_T1[0]}/telemetry?limit=1", headers=auth(role=role))
        assert r.json()["items"][0]["lat"] == 13.08
        r = await client.get(f"/api/v1/vehicles/{VIN_T1[0]}", headers=auth(role=role))
        assert r.json()["state"]["lon"] == 80.27
    for role in ("manager", "admin"):
        r = await client.get(f"/api/v1/vehicles/{VIN_T1[0]}/state", headers=auth(role=role))
        assert r.json()["lat"] == 13.082712
        r = await client.get(f"/api/v1/vehicles/{VIN_T1[0]}/telemetry?limit=1", headers=auth(role=role))
        assert r.json()["items"][0]["lat"] == 13.08271


async def test_vehicle_detail_and_state_404(client, redis, headers):
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[1]}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] is None and body["nominal_range_km"] == 320.0 and body["oem"] == "OEM_A"
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[1]}/state", headers=headers)
    assert r.status_code == 404
    r = await client.get("/api/v1/vehicles/NOPE/state", headers=headers)
    assert r.status_code == 404
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[0].lower()}", headers=headers)  # case-insensitive VIN
    assert r.status_code == 200


async def test_nearest_chargers_geosearch(client, redis, headers, data):
    from orbit_api.state import StateStore

    assert await StateStore(redis).load_chargers(data.chargers) == 4
    await put_state(redis, VIN_T1[0], soc_pct=10)  # 320 km * 10% = 32 km range
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[0]}/nearest-chargers", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    ids = [c["charger_id"] for c in items]
    assert ids == [1, 2]  # tenant-2 private charger 3 hidden; charger 4 is > 32 km away
    assert items[0]["distance_km"] < items[1]["distance_km"]
    assert items[0]["reachable"] and items[1]["reachable"]
    assert items[0]["price_per_kwh"] is None and items[1]["price_per_kwh"] == 16.0
    assert {"charger_id", "name", "lat", "lon", "max_kw", "distance_km", "reachable", "price_per_kwh"} <= set(
        items[0]
    )
    # 0.5% soc -> 1.6 km range: only the depot charger is inside the (min 1 km, here 1.6 km) search radius
    await put_state(redis, VIN_T1[0], soc_pct=0.5)
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[0]}/nearest-chargers?limit=1", headers=headers)
    assert [c["charger_id"] for c in r.json()["items"]] == [1]


async def test_nearest_chargers_without_state_uses_depot(client, redis, headers, data):
    from orbit_api.state import StateStore

    await StateStore(redis).load_chargers(data.chargers)
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[1]}/nearest-chargers", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert [c["charger_id"] for c in items] == [1, 2]  # 50 km default radius from depot
    assert all(c["reachable"] for c in items)
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[2]}/nearest-chargers", headers=headers)
    assert r.status_code == 200


async def test_nearest_chargers_no_position(client, redis, headers, data):
    data.depots.clear()
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[1]}/nearest-chargers", headers=headers)
    assert r.status_code == 404


async def test_range_forecast(client, redis, headers):
    await put_state(redis, VIN_T1[0], soc_pct=25)
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[0]}/range-forecast", headers=headers)
    assert r.json() == {"range_km": 80.0, "method": "baseline_v0", "confidence": 0.5}
    await put_state(redis, VIN_T1[0], soc_pct=25, ambient_c=-2)
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[0]}/range-forecast", headers=headers)
    assert r.json()["range_km"] == 56.0 and r.json()["confidence"] == 0.6
    r = await client.get(f"/api/v1/vehicles/{VIN_T1[1]}/range-forecast", headers=headers)
    assert r.status_code == 404


async def test_fleets_and_summary(client, redis, headers, data):
    r = await client.get("/api/v1/fleets", headers=headers)
    assert r.json() == {"items": [{"id": 1, "name": "Chennai Fleet 1", "depot_id": 1, "vehicle_count": 3}]}
    r = await client.get("/api/v1/fleet/summary", headers=headers)
    assert r.status_code == 200
    assert r.json()["vehicles"] == 3 and r.json()["open_alerts"] == 5 and r.json()["online"] == 0
    await redis.hset(
        "summary:1",
        mapping={
            "vehicles": "3",
            "online": "2",
            "charging": "1",
            "at_risk": "0",
            "avg_soc": "61.2",
            "open_alerts": "5",
            "kwh_today": "12.5",
            "cost_today": "80",
            "savings_today": "20",
        },
    )
    r = await client.get("/api/v1/fleet/summary", headers=headers)
    body = r.json()
    assert body["online"] == 2 and body["avg_soc"] == 61.2
    # money fields come from today's READY plans in Postgres, not from the stream summary hash
    expected = [
        p
        for p in data.plans
        if p.get("tenant_id") == 1 and p.get("status") == "READY" and p.get("total_cost")
    ]
    assert body["cost_today"] == round(sum(float(p["total_cost"]) for p in expected), 2)


def test_merge_summaries_sums_counts_and_weights_avg_soc():
    from orbit_api.state import merge_summaries

    a = {
        "vehicles": "100",
        "online": "90",
        "charging": "5",
        "at_risk": "1",
        "avg_soc": "60",
        "soc_n": "100",
        "kwh_today": "10",
    }
    b = {
        "vehicles": "300",
        "online": "280",
        "charging": "15",
        "at_risk": "3",
        "avg_soc": "40",
        "soc_n": "300",
        "kwh_today": "2.5",
    }
    m = merge_summaries([a, b])
    assert m is not None
    assert (m["vehicles"], m["online"], m["charging"], m["at_risk"]) == (400, 370, 20, 4)
    assert m["avg_soc"] == 45.0 and m["kwh_today"] == 12.5
    assert merge_summaries([]) is None


async def test_fleet_summary_merges_replica_parts(client, redis, headers):
    for rid, n in (("r1", "2"), ("r2", "3")):
        await redis.hset(
            f"summary:1:part:{rid}", mapping={"vehicles": n, "online": n, "avg_soc": "50", "soc_n": n}
        )
        await redis.sadd("summary:1:parts", rid)
    r = await client.get("/api/v1/fleet/summary", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["vehicles"] == 5 and body["online"] == 5 and body["avg_soc"] == 50.0
    assert body["open_alerts"] == 5  # always from Postgres, never from the stream summary
