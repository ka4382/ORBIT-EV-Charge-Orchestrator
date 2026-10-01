"""Shared "request a charging plan" step: insert a PENDING charging_plan row, publish `plans.requested`,
write the PLAN_REQUESTED audit row. Used by POST /plans and by approval of an agent REPLAN action, so both
paths produce exactly the same rows and events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from .publisher import PlanPublisher
from .repositories.base import Repos, Row

log = structlog.get_logger("orbit_api.plans")


class PlanQueueError(RuntimeError):
    """`plans.requested` could not be published; callers answer 503 and the transaction rolls back."""


async def request_plan(
    repos: Repos,
    publisher: PlanPublisher,
    vehicle: Row,
    *,
    target_soc: float,
    depart_ts: datetime,
    requested_by: str,
    audit_details: dict[str, Any] | None = None,
) -> int:
    plan_id = await repos.plans.create(
        vehicle_id=vehicle["id"], target_soc=target_soc, depart_ts=depart_ts, requested_by=requested_by
    )
    event = {
        "plan_id": plan_id,
        "vin": vehicle["vin"],
        "vehicle_id": vehicle["id"],
        "tenant_id": repos.tenant_id,
        "target_soc": target_soc,
        "depart_ts": depart_ts.isoformat(),
    }
    try:
        await publisher.publish_plan_requested(event)
    except Exception as exc:
        log.error("plans.requested publish failed", plan_id=plan_id, error=str(exc))
        raise PlanQueueError(str(exc)) from exc
    await repos.audit.insert(
        actor_id=requested_by,
        action="PLAN_REQUESTED",
        resource=f"plan:{plan_id}",
        details={
            "vin": vehicle["vin"],
            "target_soc": target_soc,
            "depart_ts": depart_ts.isoformat(),
            **(audit_details or {}),
        },
    )
    return plan_id
