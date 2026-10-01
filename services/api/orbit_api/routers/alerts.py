from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..auth import Principal, require
from ..deps import ReposDep
from ..pagination import CursorQuery, LimitQuery, cursor_datetime, decode_cursor, page
from ..problem import ProblemException
from ..schemas import PROBLEM_RESPONSES, Alert, AlertAck, Page

router = APIRouter(tags=["alerts"], responses=PROBLEM_RESPONSES)
AnyRole = Annotated[Principal, Depends(require())]
Acker = Annotated[Principal, Depends(require("manager", "admin"))]
SEVERITIES = {"INFO", "WARN", "CRITICAL"}


@router.get("/alerts", response_model=Page[Alert], summary="Alert feed (keyset on ts, id)")
async def list_alerts(
    repos: ReposDep,
    principal: AnyRole,
    severity: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    acked: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, LimitQuery] = 50,
    cursor: Annotated[str | None, CursorQuery] = None,
) -> Page[Alert]:
    if severity is not None and severity.upper() not in SEVERITIES:
        raise ProblemException(
            422, f"severity must be one of {sorted(SEVERITIES)}", type_="urn:orbit:problem:validation"
        )
    key = decode_cursor(cursor)
    ck = (cursor_datetime(key[0]), int(key[1])) if key else None
    rows = await repos.alerts.find(
        severity=severity.upper() if severity else None, since=since, acked=acked, cursor=ck, limit=limit + 1
    )
    items, next_cursor = page(rows, limit, lambda r: (r["ts"].isoformat(), r["id"]))
    return Page(items=[Alert(**r) for r in items], next_cursor=next_cursor)


@router.post("/alerts/{alert_id}/ack", response_model=AlertAck, summary="Acknowledge an alert")
async def ack_alert(alert_id: int, repos: ReposDep, principal: Acker) -> AlertAck:
    r = await repos.alerts.ack(alert_id, principal.sub)
    if r is None:
        raise ProblemException(404, f"alert {alert_id} not found", type_="urn:orbit:problem:not-found")
    await repos.audit.insert(
        actor_id=principal.sub, action="ALERT_ACK", resource=f"alert:{alert_id}", details={}
    )
    return AlertAck(**r)
