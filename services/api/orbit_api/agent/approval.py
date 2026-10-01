"""Execution of an approved agent action. The agent only ever *proposes*; this runs after a human approved
via POST /agent/actions/{id}/approve, inside the approver's tenant-scoped transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from orbit_common.vin import is_valid

from ..plan_requests import PlanQueueError, request_plan
from ..problem import ProblemException
from ..publisher import PlanPublisher
from ..repositories.base import Repos, Row
from .models import AgentExecution
from .offline import next_departure
from .tools import DEFAULT_TARGET_SOC, MAX_REPLAN_VINS

EXECUTABLE_ACTIONS = frozenset({"REPLAN"})


def _depart(raw: Any, now: datetime) -> datetime:
    if isinstance(raw, str) and raw:
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return next_departure(now)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return next_departure(now)


async def execute_replan(
    repos: Repos, publisher: PlanPublisher, action: Row, *, approver: str, now: datetime | None = None
) -> AgentExecution:
    """Create one charging_plan per VIN and publish `plans.requested` exactly like POST /plans."""
    now = now or datetime.now(UTC)
    params = action.get("params") or {}
    vins = [str(v).upper() for v in (params.get("vins") or [])][:MAX_REPLAN_VINS]
    target = float(params.get("target_soc") or DEFAULT_TARGET_SOC)
    depart = _depart(params.get("depart_ts"), now)
    plan_ids: list[int] = []
    errors: list[str] = []
    if depart <= now:
        errors.append("depart_ts is in the past; approve a fresh proposal")
        vins = []
    if not vins and not errors:
        errors.append("action has no vehicles")
    for vin in vins:
        v = await repos.vehicles.get_by_vin(vin) if is_valid(vin) else None  # RLS: own tenant only
        if v is None:
            errors.append(f"{vin}: not found in this fleet")
            continue
        try:
            plan_ids.append(
                await request_plan(
                    repos,
                    publisher,
                    v,
                    target_soc=target,
                    depart_ts=depart,
                    requested_by=approver,
                    audit_details={"agent_action_id": action["id"]},
                )
            )
        except PlanQueueError as exc:
            # whole request fails -> transaction rolls back, the action stays PENDING and can be retried
            raise ProblemException(
                503, "plan request could not be queued", type_="urn:orbit:problem:unavailable"
            ) from exc
    status: Literal["EXECUTED", "FAILED"] = "EXECUTED" if plan_ids else "FAILED"
    await repos.agent.set_status(action["id"], status=status, from_status="APPROVED")
    await repos.audit.insert(
        actor_id=approver,
        action=f"AGENT_{status}",
        resource=f"agent_action:{action['id']}",
        details={"plan_ids": plan_ids, "errors": errors[:20]},
    )
    return AgentExecution(status=status, plan_ids=plan_ids, errors=errors)
