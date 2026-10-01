from __future__ import annotations

import importlib
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ORBIT_ENV", "dev")
os.environ.setdefault("JWT_DEV_SECRET", "unit-test-secret")
os.environ.setdefault("JWKS_URL", "")
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "")

from api_fakes import FakeData, FakePublisher, fake_repos, seed  # noqa: E402
from fastapi import Request  # noqa: E402
from orbit_api.audit import AuditEntry, AuditQueue  # noqa: E402
from orbit_api.auth import CurrentPrincipal  # noqa: E402
from orbit_api.deps import get_repos  # noqa: E402
from orbit_api.main import Resources, create_app  # noqa: E402
from orbit_api.ratelimit import RateLimiter  # noqa: E402


@pytest.fixture
def data() -> FakeData:
    return seed()


@pytest.fixture
def redis() -> Any:
    fakeredis = importlib.import_module("fakeredis.aioredis")  # dynamic: keeps numpy out of mypy's graph
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def publisher() -> FakePublisher:
    return FakePublisher()


@pytest.fixture
def audit_rows() -> list[AuditEntry]:
    return []


@pytest.fixture
def resources(redis, publisher, audit_rows) -> Resources:
    async def sink(batch: list[AuditEntry]) -> None:
        audit_rows.extend(batch)

    return Resources(
        db=None,
        redis=redis,
        publisher=publisher,
        audit=AuditQueue(sink, flush_interval_s=0.01),
        rate_limiter=RateLimiter(redis, capacity=1000),
        preset=True,
    )


@pytest.fixture
def app(resources, data):
    """App wired to fakes. `get_repos` is overridden with the same (Request, CurrentPrincipal) signature so
    the auth + rate-limit chain still runs, but repositories come from the in-memory FakeData."""
    application = create_app(resources)

    async def override(request: Request, principal: CurrentPrincipal) -> AsyncIterator[Any]:
        yield fake_repos(data, principal.tenant_id)

    application.dependency_overrides[get_repos] = override
    return application


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


from api_auth import auth  # noqa: E402


@pytest.fixture
def headers() -> dict[str, str]:
    return auth()


@pytest.fixture
def sync_client(app) -> Iterator[Any]:
    from starlette.testclient import TestClient

    with TestClient(app) as c:
        yield c
