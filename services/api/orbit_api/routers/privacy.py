from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from ..auth import Principal, require
from ..deps import ReposDep
from ..problem import ProblemException
from ..schemas import PROBLEM_RESPONSES, ErasureAccepted

router = APIRouter(tags=["privacy"], responses=PROBLEM_RESPONSES)
Admin = Annotated[Principal, Depends(require("admin"))]


@router.delete(
    "/privacy/drivers/{driver_id}",
    response_model=ErasureAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Right-to-erasure for a driver (audited)",
)
async def erase_driver(driver_id: int, repos: ReposDep, principal: Admin) -> ErasureAccepted:
    if not await repos.privacy.erase_driver(driver_id):
        raise ProblemException(404, f"driver {driver_id} not found", type_="urn:orbit:problem:not-found")
    job_id = str(uuid.uuid4())
    await repos.audit.insert(
        actor_id=principal.sub, action="ERASURE", resource=f"driver:{driver_id}", details={"job_id": job_id}
    )
    return ErasureAccepted(job_id=job_id)
