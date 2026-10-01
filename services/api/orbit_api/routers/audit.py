from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ..auth import require
from ..deps import ReposDep
from ..pagination import CursorQuery, LimitQuery, cursor_datetime, decode_cursor, page
from ..schemas import PROBLEM_RESPONSES, AuditRow, Page

router = APIRouter(
    tags=["audit"], dependencies=[Depends(require("admin", "auditor"))], responses=PROBLEM_RESPONSES
)


@router.get("/audit", response_model=Page[AuditRow], summary="Audit trail (keyset on ts, id)")
async def list_audit(
    repos: ReposDep, limit: Annotated[int, LimitQuery] = 50, cursor: Annotated[str | None, CursorQuery] = None
) -> Page[AuditRow]:
    key = decode_cursor(cursor)
    ck = (cursor_datetime(key[0]), int(key[1])) if key else None
    rows = await repos.audit.find(cursor=ck, limit=limit + 1)
    items, next_cursor = page(rows, limit, lambda r: (r["ts"].isoformat(), r["id"]))
    return Page(items=[AuditRow(**r) for r in items], next_cursor=next_cursor)
