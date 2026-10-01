from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from ..agent import AgentConfig, AgentQueryResponse, AgentService, load_config
from ..agent.approval import EXECUTABLE_ACTIONS, execute_replan
from ..agent.models import AgentDecisionResult
from ..auth import Principal, require
from ..deps import ReposDep
from ..problem import ProblemException
from ..schemas import PROBLEM_RESPONSES, AgentDecision, AgentQuery

router = APIRouter(tags=["agent"], responses=PROBLEM_RESPONSES)
Operator = Annotated[Principal, Depends(require("manager", "admin"))]


def get_agent_config() -> AgentConfig:
    """Read per request so ANTHROPIC_API_KEY / AGENT_* changes apply without a restart. Tests override."""
    return load_config()


AgentConfigDep = Annotated[AgentConfig, Depends(get_agent_config)]


@router.post(
    "/agent/query",
    response_model=AgentQueryResponse,
    summary="Ask the fleet agent (LLM mode with ANTHROPIC_API_KEY, deterministic offline mode otherwise)",
    responses={422: PROBLEM_RESPONSES[404]},
)
async def agent_query(
    body: AgentQuery, request: Request, repos: ReposDep, principal: Operator, cfg: AgentConfigDep
) -> AgentQueryResponse:
    store = getattr(request.app.state, "store", None)  # optional: tools degrade without live state
    return await AgentService(cfg).query(body.question, repos=repos, store=store, principal=principal)


def _missing(action_id: int) -> ProblemException:
    return ProblemException(404, f"agent action {action_id} not found", type_="urn:orbit:problem:not-found")


async def _decide(repos: ReposDep, principal: Principal, action_id: int, status: str) -> dict[str, Any]:
    r = await repos.agent.decide(action_id, status=status, decided_by=principal.sub)
    if r is None:
        existing = await repos.agent.get(action_id)
        if existing is None:
            raise _missing(action_id)
        raise ProblemException(
            409, f"agent action {action_id} already {existing['status']}", type_="urn:orbit:problem:conflict"
        )
    await repos.audit.insert(
        actor_id=principal.sub, action=f"AGENT_{status}", resource=f"agent_action:{action_id}", details={}
    )
    return r


@router.post(
    "/agent/actions/{action_id}/approve",
    response_model=AgentDecisionResult,
    response_model_exclude_none=True,
    summary="Approve a proposed action (a REPLAN is executed: plans created + plans.requested published)",
)
async def approve(
    action_id: int, request: Request, repos: ReposDep, principal: Operator
) -> AgentDecisionResult:
    action = await repos.agent.get(action_id)
    if action is None:
        raise _missing(action_id)
    publisher = getattr(request.app.state, "publisher", None)
    executable = action["action"] in EXECUTABLE_ACTIONS and action["status"] == "PENDING"
    if executable and publisher is None:
        raise ProblemException(503, "event bus unavailable", type_="urn:orbit:problem:unavailable")
    r = await _decide(repos, principal, action_id, "APPROVED")
    execution = None
    if executable:
        assert publisher is not None
        execution = await execute_replan(repos, publisher, action, approver=principal.sub)
    return AgentDecisionResult(id=r["id"], status=r["status"], execution=execution)


@router.post(
    "/agent/actions/{action_id}/reject", response_model=AgentDecision, summary="Reject a proposed action"
)
async def reject(action_id: int, repos: ReposDep, principal: Operator) -> AgentDecision:
    r = await _decide(repos, principal, action_id, "REJECTED")
    return AgentDecision(id=r["id"], status=r["status"])
