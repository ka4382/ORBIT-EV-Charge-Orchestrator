from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..auth import Principal, require
from ..deps import ReposDep
from ..pagination import CursorQuery, LimitQuery, decode_cursor, page
from ..problem import ProblemException
from ..schemas import PROBLEM_RESPONSES, BatteryHealth, CostDay, CostReport, Page

router = APIRouter(tags=["reports"], responses=PROBLEM_RESPONSES)
FinanceRole = Annotated[Principal, Depends(require("finance", "manager", "admin"))]
AnyRole = Annotated[Principal, Depends(require())]


@router.get(
    "/reports/cost", response_model=CostReport, summary="Charging cost per day vs peak-tariff baseline"
)
async def cost_report(
    repos: ReposDep,
    principal: FinanceRole,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
) -> CostReport:
    now = datetime.now(UTC)
    to_ts = to or now
    from_ts = from_ or (to_ts - timedelta(days=30))
    if from_ts >= to_ts:
        raise ProblemException(422, "from must be before to", type_="urn:orbit:problem:validation")
    rows = await repos.reports.cost_by_day(from_ts, to_ts)
    days = []
    for r in rows:
        cost, baseline = float(r["cost"]), float(r["baseline_cost"])
        days.append(
            CostDay(
                date=str(r["date"]),
                kwh=float(r["kwh"]),
                cost=cost,
                baseline_cost=baseline,
                savings=round(baseline - cost, 2),
                peak_kw=float(r["peak_kw"]),
            )
        )
    return CostReport(days=days)


@router.get(
    "/reports/battery-health",
    response_model=Page[BatteryHealth],
    summary="Latest SoH per vehicle with pgvector look-alikes",
)
async def battery_health(
    repos: ReposDep,
    principal: AnyRole,
    limit: Annotated[int, LimitQuery] = 50,
    cursor: Annotated[str | None, CursorQuery] = None,
) -> Page[BatteryHealth]:
    key = decode_cursor(cursor)
    rows = await repos.reports.battery_health(after_vin=str(key[0]) if key else None, limit=limit + 1)
    items, next_cursor = page(rows, limit, lambda r: (r["vin"],))
    return Page(
        items=[BatteryHealth(**{**r, "similar": list(r.get("similar") or [])}) for r in items],
        next_cursor=next_cursor,
    )
