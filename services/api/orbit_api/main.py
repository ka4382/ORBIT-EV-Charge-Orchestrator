"""ORBIT API application factory. `uvicorn orbit_api.main:app`."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, Response
from orbit_common.config import settings
from orbit_common.logging import configure as configure_logging
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis

from . import auth, problem
from .audit import AuditMiddleware, AuditQueue, pg_sink_factory
from .db import Database
from .middleware import RequestContextMiddleware
from .publisher import KafkaPlanPublisher, NullPlanPublisher, PlanPublisher
from .ratelimit import RateLimiter
from .repositories.postgres import all_chargers
from .routers import agent, alerts, audit, chargers, fleets, plans, privacy, reports, tariffs, vehicles, ws
from .schemas import Health, Readiness
from .state import StateStore

API_PREFIX = "/api/v1"
CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


@dataclass
class Resources:
    """Everything the lifespan would normally connect. Tests pass pre-built fakes (`preset=True`)."""

    db: Database | None = None
    redis: Redis | None = None
    publisher: PlanPublisher | None = None
    audit: AuditQueue | None = None
    rate_limiter: RateLimiter | None = None
    preset: bool = False


async def _connect(res: Resources, log: Any) -> None:
    s = settings()
    try:
        res.db = await Database.connect(s.database_app_url, s.database_url)
    except Exception as exc:  # keep serving /healthz; /readyz reports postgres=false
        log.error("postgres unavailable", error=str(exc))
    try:
        res.redis = Redis.from_url(s.redis_url, decode_responses=True)
        await res.redis.ping()
    except Exception as exc:
        log.error("redis unavailable", error=str(exc))
        res.redis = None
    try:
        kafka = KafkaPlanPublisher()
        await kafka.start()
        res.publisher = kafka
    except Exception as exc:
        log.error("kafka unavailable", error=str(exc))
        res.publisher = NullPlanPublisher()
    if res.db is not None:
        res.audit = AuditQueue(await pg_sink_factory(res.db.admin_pool))
    if res.redis is not None:
        res.rate_limiter = RateLimiter(res.redis)


async def _load_chargers(res: Resources, log: Any) -> None:
    if res.db is None or res.redis is None:
        return
    try:
        async with res.db.admin_pool.acquire() as conn:
            rows = await all_chargers(conn)
        n = await StateStore(res.redis).load_chargers(rows)
        log.info("chargers loaded into redis", count=n)
    except Exception as exc:
        log.error("charger geo index load failed", error=str(exc))


def _install_state(app: FastAPI, res: Resources) -> None:
    app.state.resources = res
    app.state.db = res.db
    app.state.redis = res.redis
    app.state.store = StateStore(res.redis) if res.redis is not None else None
    app.state.publisher = res.publisher
    app.state.audit = res.audit
    app.state.rate_limiter = res.rate_limiter


def create_app(resources: Resources | None = None) -> FastAPI:
    log = configure_logging("api")
    res = resources or Resources()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not res.preset:
            await _connect(res, log)
            await _load_chargers(res, log)
        _install_state(app, res)
        if res.audit is not None:
            await res.audit.start()
        try:
            yield
        finally:
            if res.audit is not None:
                await res.audit.stop()
            if not res.preset:
                if isinstance(res.publisher, KafkaPlanPublisher):
                    await res.publisher.stop()
                if res.redis is not None:
                    await res.redis.aclose()
                if res.db is not None:
                    await res.db.close()

    app = FastAPI(
        title="ORBIT API",
        version="1.0.0",
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        description="EV charge orchestration: fleets, live vehicle state, alerts, charging plans, reports.",
        openapi_tags=[
            {"name": "auth"},
            {"name": "fleets"},
            {"name": "vehicles"},
            {"name": "plans"},
            {"name": "alerts"},
            {"name": "chargers"},
            {"name": "tariffs"},
            {"name": "reports"},
            {"name": "agent"},
            {"name": "privacy"},
            {"name": "audit"},
            {"name": "ws"},
            {"name": "ops"},
        ],
    )
    _install_state(app, res)  # available before lifespan for tests / OpenAPI export
    problem.install(app)

    app.add_middleware(AuditMiddleware, queue_getter=lambda: app.state.audit, prefix=API_PREFIX)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    app.add_middleware(RequestContextMiddleware, hsts=settings().orbit_env != "dev", prefix=API_PREFIX)

    @app.get("/healthz", response_model=Health, tags=["ops"], summary="Liveness")
    async def healthz() -> Health:
        return Health()

    @app.get(
        "/readyz",
        response_model=Readiness,
        tags=["ops"],
        summary="Readiness of Kafka/Postgres/Redis",
        responses={503: {"model": Readiness}},
    )
    async def readyz(request: Request) -> ORJSONResponse:
        st = request.app.state
        postgres = await st.db.ping() if st.db is not None else False
        redis_ok = await st.store.ping() if st.store is not None else False
        kafka = await st.publisher.ready() if st.publisher is not None else False
        body = Readiness(kafka=kafka, postgres=postgres, redis=redis_ok)
        return ORJSONResponse(body.model_dump(), status_code=200 if all(body.model_dump().values()) else 503)

    for r in (
        auth.router,
        fleets.router,
        vehicles.router,
        plans.router,
        alerts.router,
        chargers.router,
        tariffs.router,
        reports.router,
        agent.router,
        privacy.router,
        audit.router,
        ws.router,
    ):
        app.include_router(r, prefix=API_PREFIX)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if settings().otel_exporter_otlp_endpoint:
        with contextlib.suppress(Exception):
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
    return app


app = create_app()
