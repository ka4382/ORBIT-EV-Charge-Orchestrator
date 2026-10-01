from __future__ import annotations

from fastapi import APIRouter

PROBLEM_KEYS = {"type", "title", "status", "detail", "instance", "trace_id"}


async def test_404_problem_shape(client, headers):
    r = await client.get("/api/v1/plans/999999", headers=headers)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert set(body) >= PROBLEM_KEYS
    assert body["instance"] == "/api/v1/plans/999999" and body["title"] == "Not Found"
    assert body["trace_id"] == r.headers["x-request-id"]


async def test_validation_problem(client, headers):
    r = await client.post("/api/v1/plans", headers=headers, json={"vin": "short", "target_soc": 500})
    assert r.status_code == 422
    body = r.json()
    assert body["type"] == "urn:orbit:problem:validation"
    assert any(e["loc"][-1] == "vin" for e in body["errors"])
    assert any(e["loc"][-1] == "depart_ts" for e in body["errors"])


async def test_unknown_route_is_problem(client, headers):
    r = await client.get("/api/v1/nope", headers=headers)
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")


async def test_unhandled_exception_never_leaks(app, client):
    boom = APIRouter()

    @boom.get("/boom")
    async def _boom():
        raise RuntimeError("secret internal detail")

    app.include_router(boom)
    r = await client.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["title"] == "Internal Server Error" and "secret" not in body["detail"]
    assert "Traceback" not in r.text and body["trace_id"]


async def test_request_id_is_propagated(client):
    r = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


async def test_bad_cursor(client, headers):
    r = await client.get("/api/v1/vehicles?cursor=!!!notbase64", headers=headers)
    assert r.status_code == 400 and r.json()["type"] == "urn:orbit:problem:bad-cursor"
    r = await client.get("/api/v1/vehicles?cursor=e30", headers=headers)  # {} is valid b64 but not a list
    assert r.status_code == 400


async def test_cors_preflight(client):
    r = await client.options(
        "/api/v1/me", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_metrics_endpoint(client):
    await client.get("/healthz")
    r = await client.get("/metrics")
    assert r.status_code == 200 and "orbit_api_request_seconds" in r.text


async def test_openapi_complete(client):
    spec = (await client.get("/openapi.json")).json()
    paths = set(spec["paths"])
    for p in [
        "/api/v1/fleets",
        "/api/v1/fleet/summary",
        "/api/v1/vehicles",
        "/api/v1/vehicles/{vin}",
        "/api/v1/vehicles/{vin}/state",
        "/api/v1/vehicles/{vin}/telemetry",
        "/api/v1/vehicles/{vin}/nearest-chargers",
        "/api/v1/vehicles/{vin}/range-forecast",
        "/api/v1/plans",
        "/api/v1/plans/{plan_id}",
        "/api/v1/alerts",
        "/api/v1/alerts/{alert_id}/ack",
        "/api/v1/chargers",
        "/api/v1/tariffs/current",
        "/api/v1/reports/cost",
        "/api/v1/reports/battery-health",
        "/api/v1/agent/query",
        "/api/v1/agent/actions/{action_id}/approve",
        "/api/v1/agent/actions/{action_id}/reject",
        "/api/v1/privacy/drivers/{driver_id}",
        "/api/v1/audit",
        "/api/v1/auth/dev-token",
        "/api/v1/me",
        "/healthz",
        "/readyz",
    ]:
        assert p in paths, p
    assert all(op.get("summary") for ops in spec["paths"].values() for op in ops.values())


def test_export_openapi(tmp_path):
    import json

    from export_openapi import main

    out = main(str(tmp_path / "openapi.json"))
    assert json.loads(out.read_text())["info"]["title"] == "ORBIT API"
