"""Request context (request id + structlog binding + latency metric) and security headers, pure ASGI."""

from __future__ import annotations

import time
import uuid

import structlog
from prometheus_client import Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .audit import route_template

log = structlog.get_logger("orbit_api.http")

REQUEST_LATENCY = Histogram(
    "orbit_api_request_seconds",
    "HTTP request latency",
    ["method", "route", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1, 2.5, 5),
)

SECURITY_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"no-referrer",
    b"permissions-policy": b"geolocation=(), camera=(), microphone=()",
    b"cache-control": b"no-store",
}
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool = False, prefix: str = "") -> None:
        self.app = app
        self.hsts = hsts
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        request_id = headers.get(b"x-request-id", b"").decode() or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id, method=scope.get("method", "WS"), path=scope["path"]
        )
        started = time.perf_counter()
        status_holder = {"status": 0}
        is_docs = scope["path"].startswith(DOCS_PATHS)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                raw = list(message.get("headers") or [])
                raw.append((b"x-request-id", request_id.encode()))
                if not is_docs:
                    raw.extend(SECURITY_HEADERS.items())
                    raw.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
                if self.hsts:
                    raw.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
                message["headers"] = raw
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["type"] == "http":
                elapsed = time.perf_counter() - started
                route = route_template(scope, self.prefix)
                REQUEST_LATENCY.labels(scope["method"], route, str(status_holder["status"])).observe(elapsed)
                principal = scope.get("state", {}).get("principal")
                log.info(
                    "request",
                    status=status_holder["status"],
                    duration_ms=round(elapsed * 1000, 2),
                    route=route,
                    sub=getattr(principal, "sub", None),
                    tenant_id=getattr(principal, "tenant_id", None),
                )
            structlog.contextvars.clear_contextvars()
