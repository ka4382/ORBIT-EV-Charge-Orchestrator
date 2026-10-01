from __future__ import annotations

import asyncio

from api_auth import auth
from api_fakes import VIN_T1
from orbit_api.audit import AuditEntry, AuditQueue, should_audit


def test_should_audit_rules():
    assert should_audit("POST", "/api/v1/plans", "manager")
    assert should_audit("DELETE", "/api/v1/privacy/drivers/1", "admin")
    assert not should_audit("POST", "/api/v1/auth/dev-token", None)
    assert not should_audit("GET", "/api/v1/vehicles", "manager")
    assert should_audit("GET", "/api/v1/vehicles/X/state", "auditor")
    assert should_audit("GET", "/api/v1/vehicles/X/telemetry", "finance")
    assert should_audit("GET", "/api/v1/fleet/summary", "finance")
    assert not should_audit("GET", "/api/v1/alerts", "auditor")
    assert not should_audit("GET", "/healthz", "auditor")


async def test_queue_batches_and_counts():
    batches: list[list[AuditEntry]] = []

    async def sink(batch):
        batches.append(batch)

    q = AuditQueue(sink, batch_size=3, flush_interval_s=0.02)
    await q.start()
    for i in range(7):
        q.submit(AuditEntry(1, "u", f"A{i}", "r"))
    await asyncio.sleep(0.2)
    await q.stop()
    assert sum(len(b) for b in batches) == 7 and q.written == 7 and len(batches) >= 3


async def test_flush_is_a_barrier_for_in_flight_batches():
    """An entry already taken by the writer (waiting to fill its batch) is written before flush() returns."""
    written: list[AuditEntry] = []

    async def sink(batch):
        written.extend(batch)

    q = AuditQueue(sink, batch_size=100, flush_interval_s=0.3)
    await q.start()
    q.submit(AuditEntry(1, "u", "FIRST", "r"))
    await asyncio.sleep(0.02)  # writer has dequeued FIRST and is waiting for more
    q.submit(AuditEntry(1, "u", "SECOND", "r"))
    await q.flush()
    assert {e.action for e in written} == {"FIRST", "SECOND"} and q.submitted == 2
    await q.stop()


async def test_queue_overflow_and_sink_failure():
    async def bad_sink(batch):
        raise RuntimeError("db down")

    q = AuditQueue(bad_sink, max_pending=1)
    assert q.submit(AuditEntry(1, "u", "A", "r")) and not q.submit(AuditEntry(1, "u", "B", "r"))
    assert q.dropped == 1
    await q.flush()
    assert q.dropped == 2 and q.written == 0


async def test_auditor_reads_are_logged(client, app, audit_rows, redis):
    await client.get(
        f"/api/v1/vehicles/{VIN_T1[0]}/telemetry?limit=1", headers=auth(role="auditor", sub="aud")
    )
    await client.get(
        f"/api/v1/vehicles/{VIN_T1[0]}/telemetry?limit=1", headers=auth(role="manager", sub="mgr")
    )
    await client.get("/api/v1/alerts", headers=auth(role="auditor", sub="aud"))
    await asyncio.sleep(0.05)
    await app.state.audit.flush()
    entries = [(e.actor_id, e.action, e.resource) for e in audit_rows]
    assert (
        "aud",
        "GET /api/v1/vehicles/{vin}/telemetry",
        f"/api/v1/vehicles/{VIN_T1[0]}/telemetry",
    ) in entries
    assert not any(e[0] == "mgr" for e in entries)
    assert not any("alerts" in e[2] for e in entries)
    assert all(e.details["status"] == 200 and e.details["role"] == "auditor" for e in audit_rows)


async def test_middleware_without_queue(app, client, headers):
    app.state.audit = None
    r = await client.post("/api/v1/alerts/2/ack", headers=headers)
    assert r.status_code == 200
