from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api_auth import auth
from api_fakes import NOW, VIN_T1, VIN_T2, FakePublisher


def future() -> str:
    return (datetime.now(UTC) + timedelta(hours=6)).isoformat()


async def test_post_plan_publishes_and_202(client, headers, publisher, data, audit_rows):
    body = {"vin": VIN_T1[0], "target_soc": 80, "depart_ts": future()}
    r = await client.post("/api/v1/plans", headers=headers, json=body)
    assert r.status_code == 202
    plan_id = r.json()["plan_id"]
    assert r.json() == {"plan_id": plan_id, "status": "PENDING"}
    assert len(publisher.events) == 1
    ev = publisher.events[0]
    assert set(ev) == {"plan_id", "vin", "vehicle_id", "tenant_id", "target_soc", "depart_ts"}
    assert (
        ev["plan_id"] == plan_id and ev["vin"] == VIN_T1[0] and ev["vehicle_id"] == 1 and ev["tenant_id"] == 1
    )
    assert [a["action"] for a in data.audit] == ["PLAN_REQUESTED"]
    # the plan is retrievable
    r = await client.get(f"/api/v1/plans/{plan_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "PENDING" and r.json()["slots"] == [] and r.json()["vin"] == VIN_T1[0]
    r = await client.get("/api/v1/plans?status=pending", headers=headers)
    assert [p["id"] for p in r.json()["items"]] == [plan_id] and "slots" not in r.json()["items"][0]
    r = await client.get(f"/api/v1/plans?vin={VIN_T1[1]}", headers=headers)
    assert r.json()["items"] == []
    assert (await client.get("/api/v1/plans?status=WEIRD", headers=headers)).status_code == 422


async def test_post_plan_validation(client, headers):
    r = await client.post(
        "/api/v1/plans",
        headers=headers,
        json={"vin": VIN_T1[0], "target_soc": 80, "depart_ts": "2020-01-01T00:00:00Z"},
    )
    assert r.status_code == 422 and "future" in r.json()["detail"]
    r = await client.post(
        "/api/v1/plans", headers=headers, json={"vin": VIN_T1[0], "target_soc": 0, "depart_ts": future()}
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/v1/plans", headers=headers, json={"vin": VIN_T2[0], "target_soc": 80, "depart_ts": future()}
    )
    assert r.status_code == 404  # other tenant's vehicle


async def test_post_plan_kafka_down_is_503(app, client, headers, publisher):
    publisher.fail = True
    r = await client.post(
        "/api/v1/plans", headers=headers, json={"vin": VIN_T1[0], "target_soc": 80, "depart_ts": future()}
    )
    assert r.status_code == 503 and r.json()["type"] == "urn:orbit:problem:unavailable"
    app.state.publisher = None
    r = await client.post(
        "/api/v1/plans", headers=headers, json={"vin": VIN_T1[0], "target_soc": 80, "depart_ts": future()}
    )
    assert r.status_code == 503


async def test_get_plan_with_slots(client, headers, data):
    data.plans.append(
        {
            "id": 77,
            "tenant_id": 1,
            "vehicle_id": 1,
            "vin": VIN_T1[0],
            "status": "READY",
            "target_soc": 90.0,
            "depart_ts": NOW,
            "created_ts": NOW,
            "total_cost": 130.0,
            "baseline_cost": 250.0,
            "savings": 120.0,
        }
    )
    data.slots.append(
        {
            "plan_id": 77,
            "charger_id": 1,
            "start_ts": NOW,
            "end_ts": NOW + timedelta(hours=2),
            "kw": 11.0,
            "kwh": 20.0,
            "cost": 130.0,
            "price_per_kwh": 6.5,
        }
    )
    r = await client.get("/api/v1/plans/77", headers=headers)
    body = r.json()
    assert (
        body["savings"] == 120.0
        and body["slots"][0]["price_per_kwh"] == 6.5
        and body["slots"][0]["kwh"] == 20.0
    )
    assert (await client.get("/api/v1/plans/77", headers=auth(tenant_id=2))).status_code == 404
    r = await client.get("/api/v1/plans?limit=1", headers=headers)
    assert r.json()["next_cursor"] is None and len(r.json()["items"]) == 1


async def test_ack_alert(client, headers, data):
    r = await client.post("/api/v1/alerts/1/ack", headers=headers)
    assert r.status_code == 200
    assert r.json()["id"] == 1 and r.json()["acked_by"] == "user-1" and r.json()["acked_ts"]
    first = r.json()["acked_ts"]
    r = await client.post("/api/v1/alerts/1/ack", headers=auth(sub="user-2"))  # idempotent
    assert r.json()["acked_by"] == "user-1" and r.json()["acked_ts"] == first
    assert (await client.post("/api/v1/alerts/6/ack", headers=headers)).status_code == 404  # tenant 2's
    assert (await client.post("/api/v1/alerts/1/ack", headers=auth(role="auditor"))).status_code == 403
    r = await client.get("/api/v1/alerts?acked=true", headers=headers)
    assert [a["id"] for a in r.json()["items"]] == [1]
    assert "ALERT_ACK" in [a["action"] for a in data.audit]


async def test_chargers_tariffs(client, headers, data):
    r = await client.get("/api/v1/chargers?limit=2", headers=headers)
    p1 = r.json()
    assert [c["id"] for c in p1["items"]] == [1, 2] and p1["next_cursor"]
    r = await client.get(f"/api/v1/chargers?limit=2&cursor={p1['next_cursor']}", headers=headers)
    assert [c["id"] for c in r.json()["items"]] == [4]  # 3 belongs to tenant 2 and is private
    r = await client.get("/api/v1/chargers?public=true", headers=headers)
    assert [c["id"] for c in r.json()["items"]] == [2, 4]
    r = await client.get("/api/v1/chargers?depot_id=1", headers=headers)
    assert [c["id"] for c in r.json()["items"]] == [1]
    r = await client.get("/api/v1/tariffs/current", headers=headers)
    assert r.status_code == 200
    assert r.json()["tariff_id"] == 1 and r.json()["slots"][1]["price_per_kwh"] == 12.5
    assert set(r.json()) == {"tariff_id", "name", "slots"}
    assert (await client.get("/api/v1/tariffs/current", headers=auth(tenant_id=2))).status_code == 404


async def test_reports(client, headers):
    assert (await client.get("/api/v1/reports/cost", headers=auth(role="auditor"))).status_code == 403
    r = await client.get("/api/v1/reports/cost", headers=auth(role="finance"))
    assert r.status_code == 200
    day = r.json()["days"][0]
    assert day["kwh"] == 60.0 and day["cost"] == 510.0 and day["baseline_cost"] == 750.0
    assert day["savings"] == 240.0 and day["peak_kw"] == 20.0
    r = await client.get(
        "/api/v1/reports/cost?from=2026-10-02T00:00:00Z&to=2026-10-01T00:00:00Z", headers=headers
    )
    assert r.status_code == 422
    r = await client.get(
        "/api/v1/reports/cost?from=2026-09-01T00:00:00Z&to=2026-09-02T00:00:00Z", headers=headers
    )
    assert r.json() == {"days": []}
    r = await client.get("/api/v1/reports/battery-health?limit=1", headers=headers)
    b = r.json()
    assert b["items"][0]["vin"] == VIN_T1[0] and b["items"][0]["similar"] == [VIN_T1[1]] and b["next_cursor"]
    r = await client.get(f"/api/v1/reports/battery-health?limit=1&cursor={b['next_cursor']}", headers=headers)
    assert r.json()["items"][0]["vin"] == VIN_T1[1] and r.json()["items"][0]["similar"] == []
    assert r.json()["items"][0]["trend_30d"] is None


async def test_agent_privacy_audit(client, data, audit_rows, app):
    admin = auth(role="admin", sub="root")
    r = await client.post(
        "/api/v1/agent/query", headers=admin, json={"question": "who should charge tonight?"}
    )
    assert r.status_code == 200 and r.json()["mode"] == "offline" and r.json()["audit_id"]
    r = await client.post("/api/v1/agent/actions/1/approve", headers=admin)
    assert r.json()["id"] == 1 and r.json()["status"] == "APPROVED"
    assert r.json()["execution"]["status"] == "EXECUTED"  # REPLAN approval creates the plan
    assert (await client.post("/api/v1/agent/actions/1/reject", headers=admin)).status_code == 409
    assert (await client.post("/api/v1/agent/actions/2/reject", headers=admin)).status_code == 409
    assert (await client.post("/api/v1/agent/actions/99/reject", headers=admin)).status_code == 404
    assert (
        await client.post("/api/v1/agent/actions/1/approve", headers=auth(role="finance"))
    ).status_code == 403
    data.agent_actions[1]["status"] = "PENDING"
    r = await client.post("/api/v1/agent/actions/2/reject", headers=admin)
    assert r.json() == {"id": 2, "status": "REJECTED"}

    assert (await client.delete("/api/v1/privacy/drivers/1", headers=auth(role="manager"))).status_code == 403
    assert (await client.delete("/api/v1/privacy/drivers/2", headers=admin)).status_code == 404  # tenant 2's
    r = await client.delete("/api/v1/privacy/drivers/1", headers=admin)
    assert r.status_code == 202 and len(r.json()["job_id"]) == 36
    assert (
        data.drivers[0]["name_masked"] == "ERASED" and data.drivers[0]["erased_at"] and data.assignments == []
    )
    erasure = [a for a in data.audit if a["action"] == "ERASURE"]
    assert (
        erasure
        and erasure[0]["details"]["job_id"] == r.json()["job_id"]
        and erasure[0]["resource"] == "driver:1"
    )

    assert (await client.get("/api/v1/audit", headers=auth(role="manager"))).status_code == 403
    r = await client.get("/api/v1/audit?limit=2", headers=auth(role="auditor"))
    assert r.status_code == 200 and len(r.json()["items"]) == 2 and r.json()["next_cursor"]
    r2 = await client.get(f"/api/v1/audit?limit=2&cursor={r.json()['next_cursor']}", headers=admin)
    assert {a["id"] for a in r2.json()["items"]}.isdisjoint({a["id"] for a in r.json()["items"]})
    actions = [a["action"] for a in data.audit]
    assert {"AGENT_QUERY", "AGENT_APPROVED", "AGENT_REJECTED", "ERASURE"} <= set(actions)

    # asynchronous data-access audit: writes + auditor reads landed in the sink
    await app.state.audit.flush()
    access = {(e.action, e.actor_id) for e in audit_rows}
    assert ("DELETE /api/v1/privacy/drivers/{driver_id}", "root") in access
    assert ("POST /api/v1/agent/actions/{action_id}/approve", "root") in access
    assert all(e.actor_type == "user" and e.tenant_id == 1 for e in audit_rows)


async def test_null_publisher_ready():
    from orbit_api.publisher import NullPlanPublisher

    p = NullPlanPublisher()
    assert await p.ready() is False
    try:
        await p.publish_plan_requested({"vehicle_id": 1})
    except RuntimeError:
        pass
    else:
        raise AssertionError
    assert await FakePublisher().ready() is True


def test_plan_item_flags_infeasible_targets():
    from datetime import UTC, datetime

    from orbit_api.schemas import PlanItem

    base = {"id": 1, "vin": "V", "status": "READY", "target_soc": 100, "depart_ts": datetime.now(UTC)}
    assert PlanItem(**base, achievable_soc=42).infeasible is True
    assert PlanItem(**base, achievable_soc=100).infeasible is False
    assert PlanItem(**base).infeasible is False  # unknown (legacy rows): not flagged
    assert PlanItem(**base, achievable_soc=42).model_dump()["infeasible"] is True
