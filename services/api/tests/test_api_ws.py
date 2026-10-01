from __future__ import annotations

import time
from functools import partial

import orjson
import pytest
from api_auth import token
from api_fakes import VIN_T1
from orbit_api.routers.ws import Subscription, filter_outgoing
from starlette.websockets import WebSocketDisconnect


def test_subscription_filter():
    s = Subscription()
    assert s.allows({"type": "state", "data": {"vin": "A"}})
    s.vins = {"A"}
    assert s.allows({"type": "state", "data": {"vin": "A"}})
    assert not s.allows({"type": "alert", "data": {"vin": "B"}})
    assert s.allows({"type": "summary", "data": {}})
    assert (
        filter_outgoing({"type": "state", "data": {"vin": "A", "lat": 1.23456}}, "finance")["data"]["lat"]
        == 1.23
    )
    assert filter_outgoing({"type": "alert", "data": {"lat": 1.23456}}, "finance")["data"]["lat"] == 1.23456


@pytest.mark.parametrize("query", ["", "?token=garbage", f"?token={token(ttl_s=-5)}"])
def test_ws_rejects_bad_token_with_4401(sync_client, query):
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        sync_client.websocket_connect(f"/api/v1/ws/fleet{query}") as ws,
    ):
        ws.receive_text()
    assert exc.value.code == 4401


def test_ws_forwards_tenant_events_with_filters(sync_client, app, redis):
    import asyncio

    async def publish(tenant: int, msg: dict) -> None:
        await redis.publish(f"events:{tenant}", orjson.dumps(msg).decode())

    async def resolver(tenant_id: int, fleet_id: int) -> set[str]:
        return {VIN_T1[0]} if fleet_id == 1 else set()

    app.state.fleet_vins_resolver = resolver
    loop = sync_client.portal  # anyio portal used by TestClient

    with sync_client.websocket_connect(f"/api/v1/ws/fleet?token={token(role='finance')}") as ws:
        time.sleep(0.1)
        loop.call(publish, 1, {"type": "state", "data": {"vin": VIN_T1[1], "lat": 13.123456, "lon": 80.1}})
        loop.call(publish, 2, {"type": "state", "data": {"vin": "OTHER", "lat": 1, "lon": 1}})
        msg = orjson.loads(ws.receive_text())
        assert msg["data"]["vin"] == VIN_T1[1] and msg["data"]["lat"] == 13.12  # masked for finance
        ws.send_text(orjson.dumps({"type": "subscribe", "fleet_id": 1}).decode())
        assert orjson.loads(ws.receive_text()) == {"type": "subscribed", "vins": 1}
        loop.call(publish, 1, {"type": "alert", "data": {"vin": VIN_T1[1]}})
        loop.call(publish, 1, {"type": "alert", "data": {"vin": VIN_T1[0]}})
        msg = orjson.loads(ws.receive_text())
        assert msg["data"]["vin"] == VIN_T1[0]
        ws.send_text(orjson.dumps({"type": "subscribe", "vins": [VIN_T1[2].lower()]}).decode())
        assert orjson.loads(ws.receive_text())["vins"] == 1
        ws.send_text("not json")
        assert orjson.loads(ws.receive_text())["type"] == "error"
        ws.send_text(orjson.dumps({"type": "subscribe", "fleet_id": "x"}).decode())
        assert orjson.loads(ws.receive_text())["type"] == "error"
        ws.send_text(orjson.dumps({"type": "noop"}).decode())
        loop.call(publish, 1, {"type": "plan", "data": {"vin": VIN_T1[2], "plan_id": 1, "status": "READY"}})
        assert orjson.loads(ws.receive_text())["type"] == "plan"
    asyncio.set_event_loop_policy(None)


def test_ws_summary_tick(sync_client, app, redis, monkeypatch):
    from orbit_api.routers import ws as ws_mod

    monkeypatch.setattr(ws_mod, "SUMMARY_INTERVAL_S", 0.05)
    monkeypatch.setattr(ws_mod, "PING_INTERVAL_S", 0.0)
    sync_client.portal.call(partial(redis.hset, "summary:1", mapping={"vehicles": "3", "online": "1"}))
    with sync_client.websocket_connect(f"/api/v1/ws/fleet?token={token()}") as ws:
        first = orjson.loads(ws.receive_text())
        second = orjson.loads(ws.receive_text())
        types = {first["type"], second["type"]}
        assert types == {"summary", "ping"}
        summary = first if first["type"] == "summary" else second
        assert summary["data"]["vehicles"] == 3 and summary["data"]["online"] == 1


def test_ws_without_store(sync_client, app):
    app.state.store = None
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        sync_client.websocket_connect(f"/api/v1/ws/fleet?token={token()}") as ws,
    ):
        ws.receive_text()
    assert exc.value.code == 1013
