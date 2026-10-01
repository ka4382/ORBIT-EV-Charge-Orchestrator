"""WS /ws/fleet?token=<JWT>: fan-out of Redis pub/sub `events:{tenant_id}` to the browser."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

import orjson
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..auth import Principal, verify_token
from ..masking import mask_location
from ..problem import ProblemException
from ..state import StateStore

log = structlog.get_logger("orbit_api.ws")
router = APIRouter(tags=["ws"])

WS_UNAUTHORIZED = 4401
PING_INTERVAL_S = 20.0
SUMMARY_INTERVAL_S = 5.0
FleetVinsResolver = Callable[[int, int], Awaitable[set[str]]]


class Subscription:
    """Per-connection filter: None = everything in the tenant, else an explicit VIN set."""

    def __init__(self) -> None:
        self.vins: set[str] | None = None

    def allows(self, message: dict[str, Any]) -> bool:
        if self.vins is None or message.get("type") not in ("state", "alert", "plan"):
            return True
        data = message.get("data") or {}
        return data.get("vin") in self.vins


def filter_outgoing(message: dict[str, Any], role: str) -> dict[str, Any]:
    if message.get("type") == "state" and isinstance(message.get("data"), dict):
        return {**message, "data": mask_location(role, message["data"])}
    return message


async def _default_fleet_vins(ws: WebSocket, tenant_id: int, fleet_id: int) -> set[str]:
    from ..repositories.postgres import pg_repos  # local import keeps ws importable without asyncpg wiring

    db = getattr(ws.app.state, "db", None)
    if db is None:
        return set()
    async with db.tenant(tenant_id) as conn:
        return await pg_repos(conn, tenant_id).fleets.vins(fleet_id)


async def _handle_client_message(ws: WebSocket, raw: str, principal: Principal, sub: Subscription) -> None:
    try:
        msg = orjson.loads(raw)
    except orjson.JSONDecodeError:
        await ws.send_text(orjson.dumps({"type": "error", "detail": "invalid JSON"}).decode())
        return
    if not isinstance(msg, dict) or msg.get("type") != "subscribe":
        return
    if "vins" in msg and isinstance(msg["vins"], list):
        sub.vins = {str(v).upper() for v in msg["vins"]}
    elif "fleet_id" in msg:
        try:
            fleet_id = int(msg["fleet_id"])
        except (TypeError, ValueError):
            await ws.send_text(orjson.dumps({"type": "error", "detail": "fleet_id must be an int"}).decode())
            return
        resolver: FleetVinsResolver | None = getattr(ws.app.state, "fleet_vins_resolver", None)
        sub.vins = (
            await resolver(principal.tenant_id, fleet_id)
            if resolver
            else await _default_fleet_vins(ws, principal.tenant_id, fleet_id)
        )
    await ws.send_text(
        orjson.dumps({"type": "subscribed", "vins": len(sub.vins) if sub.vins is not None else None}).decode()
    )


@router.websocket("/ws/fleet")
async def fleet_ws(ws: WebSocket) -> None:
    token = ws.query_params.get("token", "")
    try:
        principal = await verify_token(token) if token else None
    except ProblemException:
        principal = None
    await ws.accept()
    if principal is None:
        await ws.close(code=WS_UNAUTHORIZED, reason="invalid token")
        return
    store: StateStore | None = getattr(ws.app.state, "store", None)
    if store is None:
        await ws.close(code=1013, reason="state store unavailable")
        return
    sub = Subscription()
    channel = f"events:{principal.tenant_id}"
    pubsub = store.redis.pubsub()
    await pubsub.subscribe(channel)
    log.info("ws connected", sub=principal.sub, tenant_id=principal.tenant_id)

    async def reader() -> None:
        while True:
            raw = await ws.receive_text()
            await _handle_client_message(ws, raw, principal, sub)

    async def forwarder() -> None:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                await asyncio.sleep(0.05)
                continue
            try:
                payload = orjson.loads(msg["data"])
            except (orjson.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict) and sub.allows(payload):
                await ws.send_text(orjson.dumps(filter_outgoing(payload, principal.role)).decode())

    async def ticker() -> None:
        last_ping = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(SUMMARY_INTERVAL_S)
            summary = await store.summary(principal.tenant_id)
            if summary is not None:
                await ws.send_text(orjson.dumps({"type": "summary", "data": summary}).decode())
            now = asyncio.get_running_loop().time()
            if now - last_ping >= PING_INTERVAL_S:
                last_ping = now
                await ws.send_text(orjson.dumps({"type": "ping", "ts": now}).decode())

    tasks = [asyncio.create_task(reader()), asyncio.create_task(forwarder()), asyncio.create_task(ticker())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            exc = t.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                log.warning("ws task failed", error=repr(exc))
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(BaseException):
                await t
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        if ws.client_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await ws.close()
