from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api_auth import auth
from api_fakes import VIN_T1
from orbit_api.pagination import cursor_datetime, decode_cursor, encode_cursor, page
from orbit_api.problem import ProblemException


def test_cursor_roundtrip():
    key = ("2026-10-01T12:00:00+00:00", 42)
    c = encode_cursor(key)
    assert "=" not in c and decode_cursor(c) == key
    assert decode_cursor(None) is None and decode_cursor("") is None
    assert cursor_datetime("2026-10-01T12:00:00+00:00") == datetime(2026, 10, 1, 12, tzinfo=UTC)
    with pytest.raises(ProblemException):
        cursor_datetime("not-a-date")
    with pytest.raises(ProblemException):
        decode_cursor("@@@")


def test_page_helper():
    rows = [{"id": i} for i in range(4)]
    items, nxt = page(rows, 3, lambda r: (r["id"],))
    assert [r["id"] for r in items] == [0, 1, 2] and decode_cursor(nxt) == (2,)
    items, nxt = page(rows[:2], 3, lambda r: (r["id"],))
    assert len(items) == 2 and nxt is None
    assert page([], 3, lambda r: (r["id"],)) == ([], None)


async def test_vehicles_keyset_roundtrip(client, headers):
    r1 = await client.get("/api/v1/vehicles?limit=2", headers=headers)
    assert r1.status_code == 200
    p1 = r1.json()
    assert [v["vin"] for v in p1["items"]] == VIN_T1[:2] and p1["next_cursor"]
    r2 = await client.get(f"/api/v1/vehicles?limit=2&cursor={p1['next_cursor']}", headers=headers)
    p2 = r2.json()
    assert [v["vin"] for v in p2["items"]] == VIN_T1[2:] and p2["next_cursor"] is None


async def test_telemetry_keyset_roundtrip(client, headers):
    seen: list[int] = []
    cursor = None
    for _ in range(10):
        url = f"/api/v1/vehicles/{VIN_T1[0]}/telemetry?limit=50" + (f"&cursor={cursor}" if cursor else "")
        r = await client.get(url, headers=headers)
        assert r.status_code == 200
        body = r.json()
        seen += [row["seq"] for row in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(seen, reverse=True) and len(seen) == 120 and len(set(seen)) == 120


async def test_telemetry_time_filters(client, headers):
    r = await client.get(
        f"/api/v1/vehicles/{VIN_T1[0]}/telemetry?from=2026-10-01T11:59:30Z&to=2026-10-01T12:00:00Z",
        headers=headers,
    )
    assert r.status_code == 200 and len(r.json()["items"]) == 31


async def test_alerts_keyset_and_filters(client, headers):
    r = await client.get("/api/v1/alerts?limit=2", headers=headers)
    p1 = r.json()
    assert len(p1["items"]) == 2 and p1["next_cursor"]
    r = await client.get(f"/api/v1/alerts?limit=2&cursor={p1['next_cursor']}", headers=headers)
    p2 = r.json()
    assert {a["id"] for a in p1["items"]}.isdisjoint({a["id"] for a in p2["items"]})
    r = await client.get("/api/v1/alerts?severity=critical", headers=headers)
    assert all(a["severity"] == "CRITICAL" for a in r.json()["items"]) and r.json()["items"]
    r = await client.get("/api/v1/alerts?acked=true", headers=headers)
    assert r.json()["items"] == []
    r = await client.get("/api/v1/alerts?since=2026-10-01T11:58:30Z", headers=headers)
    assert len(r.json()["items"]) == 2
    assert (await client.get("/api/v1/alerts?severity=LOUD", headers=headers)).status_code == 422
    assert (await client.get("/api/v1/alerts?limit=0", headers=headers)).status_code == 422


async def test_alerts_tenant_scoped(client):
    r = await client.get("/api/v1/alerts", headers=auth(tenant_id=2))
    assert [a["id"] for a in r.json()["items"]] == [6, 7]
