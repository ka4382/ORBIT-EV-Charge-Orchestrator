"""Compliance audit trail. Domain actions are written synchronously by repositories (same transaction);
data-access events (every write request, every telemetry/state read by auditor/finance) are queued here
and flushed to `audit_log` in batches by a background task so the request path pays ~nothing."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger("orbit_api.audit")

AUDITED_READ_ROLES = frozenset({"auditor", "finance"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
EXEMPT_PATHS = frozenset({"/api/v1/auth/dev-token"})


@dataclass(slots=True)
class AuditEntry:
    tenant_id: int | None
    actor_id: str
    action: str
    resource: str
    actor_type: str = "user"
    details: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


AuditSink = Callable[[list[AuditEntry]], Awaitable[None]]


async def pg_sink_factory(pool: Any) -> AuditSink:
    async def sink(batch: list[AuditEntry]) -> None:
        async with pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, resource, ts, details) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                [
                    (e.tenant_id, e.actor_type, e.actor_id, e.action, e.resource, e.ts, e.details)
                    for e in batch
                ],
            )

    return sink


class AuditQueue:
    """Bounded queue + batching writer. `submit` never blocks the request; overflow is logged and counted."""

    def __init__(
        self,
        sink: AuditSink,
        *,
        batch_size: int = 200,
        flush_interval_s: float = 0.5,
        max_pending: int = 10_000,
    ) -> None:
        self._sink = sink
        self._batch_size = batch_size
        self._flush_interval = flush_interval_s
        self._queue: asyncio.Queue[AuditEntry] = asyncio.Queue(maxsize=max_pending)
        self._task: asyncio.Task[None] | None = None
        self.dropped = 0
        self.written = 0
        self.submitted = 0
        self._caught_up = asyncio.Event()

    def submit(self, entry: AuditEntry) -> bool:
        self.submitted += 1
        try:
            self._queue.put_nowait(entry)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            log.warning("audit queue full, entry dropped", action=entry.action)
            return False

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="audit-writer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.flush()

    async def flush(self, timeout_s: float = 5.0) -> None:
        """Barrier: write whatever is queued and wait for any batch the writer task already holds."""
        batch: list[AuditEntry] = []
        while not self._queue.empty() and len(batch) < self._batch_size * 10:
            batch.append(self._queue.get_nowait())
        if batch:
            await self._write(batch)
        self._caught_up.clear()
        if self.written + self.dropped < self.submitted:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._caught_up.wait(), timeout_s)

    async def _write(self, batch: list[AuditEntry]) -> None:
        try:
            await self._sink(batch)
            self.written += len(batch)
        except Exception:
            self.dropped += len(batch)
            log.exception("audit batch write failed", size=len(batch))
        if self.written + self.dropped >= self.submitted:
            self._caught_up.set()

    async def _run(self) -> None:
        while True:
            batch = [await self._queue.get()]
            try:
                deadline = asyncio.get_running_loop().time() + self._flush_interval
                while len(batch) < self._batch_size:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        batch.append(await asyncio.wait_for(self._queue.get(), timeout=remaining))
                    except TimeoutError:
                        break
            except asyncio.CancelledError:
                await self._write(batch)  # shutdown: never drop what was already dequeued
                raise
            await self._write(batch)


def route_template(scope: Scope, prefix: str = "") -> str:
    """Matched route path with `{params}`; routers included under a prefix report the un-prefixed path."""
    path: str = scope["path"]
    template = getattr(scope.get("route"), "path", None)
    if not template:
        return path
    if prefix and path.startswith(prefix) and not template.startswith(prefix):
        template = prefix + template
    return template


def should_audit(method: str, path: str, role: str | None) -> bool:
    if not path.startswith("/api/v1/") or path in EXEMPT_PATHS:
        return False
    if method in WRITE_METHODS:
        return True
    is_vehicle_data = path.startswith("/api/v1/vehicles") or path == "/api/v1/fleet/summary"
    return role in AUDITED_READ_ROLES and is_vehicle_data


class AuditMiddleware:
    """Pure-ASGI: after the route ran, log method+path+sub+tenant for audited requests."""

    def __init__(self, app: ASGIApp, queue_getter: Callable[[], AuditQueue | None], prefix: str = "") -> None:
        self.app = app
        self._queue_getter = queue_getter
        self._prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status_holder = {"status": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            principal = scope.get("state", {}).get("principal")
            method, path = scope["method"], scope["path"]
            role = getattr(principal, "role", None)
            queue = self._queue_getter()
            if principal is not None and queue is not None and should_audit(method, path, role):
                template = route_template(scope, self._prefix)
                queue.submit(
                    AuditEntry(
                        tenant_id=principal.tenant_id,
                        actor_id=principal.sub,
                        action=f"{method} {template}",
                        resource=path,
                        details={
                            "status": status_holder["status"],
                            "role": role,
                            "request_id": scope.get("state", {}).get("request_id"),
                        },
                    )
                )
