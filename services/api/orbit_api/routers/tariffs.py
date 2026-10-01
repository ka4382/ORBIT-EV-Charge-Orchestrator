from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require
from ..deps import ReposDep
from ..problem import ProblemException
from ..schemas import PROBLEM_RESPONSES, Tariff

router = APIRouter(tags=["tariffs"], dependencies=[Depends(require())], responses=PROBLEM_RESPONSES)


@router.get("/tariffs/current", response_model=Tariff, summary="Tenant's active time-of-use tariff")
async def current_tariff(repos: ReposDep) -> Tariff:
    t = await repos.tariffs.current()
    if t is None:
        raise ProblemException(404, "no tariff configured for tenant", type_="urn:orbit:problem:not-found")
    return Tariff(**t)
