from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from ..auth import Principal, require
from ..deps import PublisherDep, ReposDep
from ..pagination import CursorQuery, LimitQuery, cursor_datetime, decode_cursor, page
from ..plan_requests import PlanQueueError, request_plan
from ..problem import ProblemException
from ..schemas import PROBLEM_RESPONSES, Page, PlanAccepted, PlanCreate, PlanDetail, PlanItem, PlanSlot

router = APIRouter(tags=["plans"], responses=PROBLEM_RESPONSES)
AnyRole = Annotated[Principal, Depends(require())]
Planner = Annotated[Principal, Depends(require("manager", "admin"))]
PLAN_STATUSES = {"PENDING", "READY", "ACTIVE", "DONE", "CANCELLED", "FAILED"}


@router.post(
    "/plans",
    response_model=PlanAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a charging plan (async: publishes plans.requested)",
)
async def create_plan(
    body: PlanCreate, repos: ReposDep, publisher: PublisherDep, principal: Planner
) -> PlanAccepted:
    v = await repos.vehicles.get_by_vin(body.vin.upper())
    if v is None:
        raise ProblemException(404, f"vehicle {body.vin} not found", type_="urn:orbit:problem:not-found")
    depart = body.depart_ts if body.depart_ts.tzinfo else body.depart_ts.replace(tzinfo=UTC)
    if depart <= datetime.now(UTC):
        raise ProblemException(422, "depart_ts must be in the future", type_="urn:orbit:problem:validation")
    try:
        plan_id = await request_plan(
            repos, publisher, v, target_soc=body.target_soc, depart_ts=depart, requested_by=principal.sub
        )
    except PlanQueueError as exc:
        raise ProblemException(
            503, "plan request could not be queued", type_="urn:orbit:problem:unavailable"
        ) from exc
    return PlanAccepted(plan_id=plan_id, status="PENDING")


@router.get("/plans", response_model=Page[PlanItem], summary="List charging plans")
async def list_plans(
    repos: ReposDep,
    principal: AnyRole,
    vin: Annotated[str | None, Query()] = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, LimitQuery] = 50,
    cursor: Annotated[str | None, CursorQuery] = None,
) -> Page[PlanItem]:
    if status_ is not None and status_.upper() not in PLAN_STATUSES:
        raise ProblemException(
            422, f"status must be one of {sorted(PLAN_STATUSES)}", type_="urn:orbit:problem:validation"
        )
    key = decode_cursor(cursor)
    ck = (cursor_datetime(key[0]), int(key[1])) if key else None
    rows = await repos.plans.find(
        vin=vin.upper() if vin else None,
        status=status_.upper() if status_ else None,
        cursor=ck,
        limit=limit + 1,
    )
    items, next_cursor = page(rows, limit, lambda r: (r["created_ts"].isoformat(), r["id"]))
    return Page(items=[PlanItem(**r) for r in items], next_cursor=next_cursor)


@router.get("/plans/{plan_id}", response_model=PlanDetail, summary="Plan with slots and prices")
async def get_plan(plan_id: int, repos: ReposDep, principal: AnyRole) -> PlanDetail:
    p = await repos.plans.get(plan_id)
    if p is None:
        raise ProblemException(404, f"plan {plan_id} not found", type_="urn:orbit:problem:not-found")
    slots = [PlanSlot(**s) for s in await repos.plans.slots(plan_id)]
    return PlanDetail(**{**p, "slots": slots})
