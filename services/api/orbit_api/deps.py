"""FastAPI dependencies wiring ports to adapters. Tests override these with fakes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request

from .auth import CurrentPrincipal, Principal
from .problem import ProblemException
from .publisher import PlanPublisher
from .repositories.base import Repos
from .repositories.postgres import pg_repos
from .state import StateStore


async def get_repos(request: Request, principal: CurrentPrincipal) -> AsyncIterator[Repos]:
    """One RLS-scoped transaction per request: `SET LOCAL app.tenant_id` then the repositories."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise ProblemException(503, "database unavailable", type_="urn:orbit:problem:unavailable")
    async with db.tenant(principal.tenant_id) as conn:
        yield pg_repos(conn, principal.tenant_id)


def get_state_store(request: Request) -> StateStore:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise ProblemException(503, "state store unavailable", type_="urn:orbit:problem:unavailable")
    return store


def get_publisher(request: Request) -> PlanPublisher:
    pub = getattr(request.app.state, "publisher", None)
    if pub is None:
        raise ProblemException(503, "event bus unavailable", type_="urn:orbit:problem:unavailable")
    return pub


# scope="function": commit the request transaction BEFORE the response is sent. With the default scope the
# commit ran after the response, so a client that POSTed /plans and immediately GET the new id could see 404
# (read-your-writes violation found by the BDD suite).
ReposDep = Annotated[Repos, Depends(get_repos, scope="function")]
StoreDep = Annotated[StateStore, Depends(get_state_store)]
PublisherDep = Annotated[PlanPublisher, Depends(get_publisher)]
PrincipalDep = Annotated[Principal, Depends(lambda request: request.state.principal)]
