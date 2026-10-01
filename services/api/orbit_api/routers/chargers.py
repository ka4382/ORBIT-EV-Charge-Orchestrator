from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..auth import require
from ..deps import ReposDep
from ..pagination import CursorQuery, LimitQuery, decode_cursor, page
from ..schemas import PROBLEM_RESPONSES, Charger, Page

router = APIRouter(tags=["chargers"], dependencies=[Depends(require())], responses=PROBLEM_RESPONSES)


@router.get(
    "/chargers", response_model=Page[Charger], summary="Chargers visible to the tenant (own + public)"
)
async def list_chargers(
    repos: ReposDep,
    depot_id: Annotated[int | None, Query()] = None,
    public: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, LimitQuery] = 50,
    cursor: Annotated[str | None, CursorQuery] = None,
) -> Page[Charger]:
    key = decode_cursor(cursor)
    rows = await repos.chargers.find(
        depot_id=depot_id, public=public, after_id=int(key[0]) if key else None, limit=limit + 1
    )
    items, next_cursor = page(rows, limit, lambda r: (r["id"],))
    return Page(items=[Charger(**r) for r in items], next_cursor=next_cursor)
