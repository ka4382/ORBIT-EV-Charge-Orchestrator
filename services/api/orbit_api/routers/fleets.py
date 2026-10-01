from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require
from ..deps import ReposDep, StoreDep
from ..schemas import PROBLEM_RESPONSES, Fleet, FleetSummary, Items

router = APIRouter(tags=["fleets"], dependencies=[Depends(require())], responses=PROBLEM_RESPONSES)


@router.get("/fleets", response_model=Items[Fleet], summary="List fleets with vehicle counts")
async def list_fleets(repos: ReposDep) -> Items[Fleet]:
    return Items(items=[Fleet(**r) for r in await repos.fleets.find()])


@router.get(
    "/fleet/summary",
    response_model=FleetSummary,
    summary="Live fleet summary: stream counts merged from state replicas, money and alerts from Postgres",
)
async def fleet_summary(repos: ReposDep, store: StoreDep) -> FleetSummary:
    live = await store.summary(repos.tenant_id) or {"vehicles": await repos.vehicles.count()}
    cost, savings = await repos.plans.totals_today()
    live.update(
        open_alerts=await repos.alerts.count_open(),
        cost_today=round(cost, 2),
        savings_today=round(savings, 2),
    )
    return FleetSummary(**live)
