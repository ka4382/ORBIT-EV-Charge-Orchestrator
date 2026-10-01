"""propose_replan only proposes; approval executes REPLAN through the same path as POST /plans."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from api_agent_support import EXTRA_T1, FOREIGN_VIN, VALID_T1, prepare_agent_app
from api_auth import auth
from api_fakes import VIN_T1, fake_repos
from orbit_api.agent.approval import execute_replan
from orbit_api.agent.tools import ROLE_TOOLS, ToolContext, ToolExecutor
from orbit_api.auth import Principal
from orbit_api.state import StateStore

URL = "/api/v1/agent/query"


@pytest.fixture
async def agent_app(app, data, redis):
    return await prepare_agent_app(app, data, redis)


def executor(data, redis=None, role: str = "manager", tenant: int = 1, max_calls: int = 5) -> ToolExecutor:
    ctx = ToolContext(
        repos=fake_repos(data, tenant),
        store=StateStore(redis) if redis is not None else None,
        principal=Principal(sub="u", tenant_id=tenant, role=role),
        run_id="agent-run-test",
    )
    return ToolExecutor(ctx, max_calls=max_calls)


def depart(hours: float = 12) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


async def test_offline_schedule_creates_pending_action_only(agent_app, client, data, publisher):
    plans_before = len(data.plans)
    r = await client.post(
        URL, headers=auth(), json={"question": "Schedule charging for the at-risk vehicles"}
    )
    body = r.json()
    assert r.status_code == 200 and len(body["actions"]) == 1
    act = body["actions"][0]
    assert act["action"] == "REPLAN" and act["status"] == "PENDING"
    assert act["params"]["vins"] == [VIN_T1[0], EXTRA_T1[0]] and act["params"]["target_soc"] == 80.0
    assert "approves" in body["answer"] and f"#{act['id']}" in body["answer"]
    stored = next(a for a in data.agent_actions if a["id"] == act["id"])
    assert stored["status"] == "PENDING" and stored["tenant_id"] == 1
    assert stored["proposed_by"].startswith("agent-run-")
    assert len(data.plans) == plans_before and publisher.events == []  # nothing executed

    # approval executes: one plan per VIN, published like POST /plans
    r = await client.post(f"/api/v1/agent/actions/{act['id']}/approve", headers=auth(sub="boss"))
    res = r.json()
    assert r.status_code == 200 and res["status"] == "APPROVED"
    assert res["execution"]["status"] == "EXECUTED" and len(res["execution"]["plan_ids"]) == 2
    assert [e["vin"] for e in publisher.events] == [VIN_T1[0], EXTRA_T1[0]]
    ev = publisher.events[0]
    assert set(ev) == {"plan_id", "vin", "vehicle_id", "tenant_id", "target_soc", "depart_ts"}
    assert ev["tenant_id"] == 1 and ev["target_soc"] == 80.0 and ev["depart_ts"] == act["params"]["depart_ts"]
    new_plans = [p for p in data.plans if p["id"] in res["execution"]["plan_ids"]]
    assert all(p["status"] == "PENDING" and p["requested_by"] == "boss" for p in new_plans)
    assert stored["status"] == "EXECUTED"
    audit = [a["action"] for a in data.audit]
    assert audit.count("PLAN_REQUESTED") == 2 and "AGENT_APPROVED" in audit and "AGENT_EXECUTED" in audit
    req = [a for a in data.audit if a["action"] == "PLAN_REQUESTED"]
    assert all(a["details"]["agent_action_id"] == act["id"] for a in req)
    # executed actions cannot be approved or rejected again
    assert (
        await client.post(f"/api/v1/agent/actions/{act['id']}/approve", headers=auth())
    ).status_code == 409


async def test_reject_does_not_execute(agent_app, client, data, publisher):
    body = (await client.post(URL, headers=auth(), json={"question": "replan the at-risk vans"})).json()
    aid = body["actions"][0]["id"]
    r = await client.post(f"/api/v1/agent/actions/{aid}/reject", headers=auth())
    assert r.json() == {"id": aid, "status": "REJECTED"} and publisher.events == []


async def test_approve_with_publisher_down_rolls_back(agent_app, client, data, publisher):
    body = (await client.post(URL, headers=auth(), json={"question": "schedule charging"})).json()
    aid = body["actions"][0]["id"]
    publisher.fail = True
    r = await client.post(f"/api/v1/agent/actions/{aid}/approve", headers=auth())
    assert r.status_code == 503 and r.json()["type"] == "urn:orbit:problem:unavailable"


async def test_approve_without_event_bus(agent_app, client, data, app):
    app.state.publisher = None
    r = await client.post("/api/v1/agent/actions/1/approve", headers=auth())
    assert r.status_code == 503 and data.agent_actions[0]["status"] == "PENDING"
    assert (await client.post("/api/v1/agent/actions/404/approve", headers=auth())).status_code == 404


async def test_propose_replan_guards(data, redis):
    from api_agent_support import enrich

    enrich(data)
    ex = executor(data, redis)
    ok = {"vins": [VIN_T1[0]], "target_soc": 80, "depart_ts": depart(), "rationale": "cheap night window"}

    c = await ex.call("propose_replan", {**ok, "vins": [VIN_T1[0], FOREIGN_VIN]})
    assert not c.ok and "not in your fleet" in c.error and FOREIGN_VIN in c.error
    c = await ex.call("propose_replan", {**ok, "vins": ["1HGCM82633A004353"]})
    assert not c.ok and "not a valid VIN" in c.error
    c = await ex.call("propose_replan", {**ok, "tenant_id": 2})
    assert not c.ok and "tenant_id" in c.error  # no tenant parameter exists; extras are rejected
    c = await ex.call("propose_replan", {**ok, "depart_ts": depart(-1)})
    assert not c.ok and "depart_ts" in c.error
    assert all(a["id"] <= 2 for a in data.agent_actions)  # nothing inserted so far
    c = await ex.call("propose_replan", {**ok, "vins": [VIN_T1[0].lower(), VIN_T1[0]]})
    assert c.ok and c.data["vehicles"] == 1 and ex.actions[0]["params"]["vins"] == [VIN_T1[0]]

    too_many = executor(data, redis)
    c = await too_many.call("propose_replan", {**ok, "vins": [VIN_T1[0]] * 51})
    assert not c.ok and "50" in c.error


async def test_cross_tenant_vin_rejected_through_rls_lookup(data, redis):
    ex = executor(data, redis, tenant=2)
    c = await ex.call(
        "propose_replan",
        {"vins": [VIN_T1[0]], "target_soc": 80, "depart_ts": depart(), "rationale": "x"},
    )
    assert not c.ok and "not in your fleet" in c.error
    c = await ex.call("get_vehicle_state", {"vin": VIN_T1[0]})
    assert not c.ok and "not in your fleet" in c.error


async def test_role_allowlist_and_unknown_tools(data):
    fin = executor(data, role="finance")
    assert {s["name"] for s in fin.specs()} == ROLE_TOOLS["finance"]
    c = await fin.call("propose_replan", {})
    assert not c.ok and "not available to role finance" in c.error
    c = await fin.call("drop_tables", {})
    assert not c.ok
    mgr = executor(data)
    assert "propose_replan" in {s["name"] for s in mgr.specs()}
    for spec in mgr.specs():
        props = spec["input_schema"]["properties"]
        assert "tenant_id" not in props and "role" not in props
        assert spec["input_schema"]["additionalProperties"] is False


async def test_tool_call_cap(data, redis):
    ex = executor(data, redis, max_calls=5)
    for _ in range(5):
        assert (await ex.call("get_fleet_summary", {})).ok
    c = await ex.call("get_fleet_summary", {})
    assert not c.ok and c.error == "tool_call_cap" and ex.executed == 5 and ex.budget_left == 0
    rows = [a for a in data.audit if a["action"] == "AGENT_TOOL"]
    assert len(rows) == 6 and rows[-1]["details"]["error"] == "tool_call_cap"


async def test_tools_without_state_store(data):
    ex = executor(data)
    assert (await ex.call("nearest_chargers", {"vin": VIN_T1[0]})).error == "live state store unavailable"
    c = await ex.call("list_at_risk_vehicles", {})
    assert c.ok and c.data["without_live_state"] == 3 and c.data["at_risk_count"] == 0
    c = await ex.call("get_vehicle_state", {"vin": VIN_T1[0]})
    assert c.ok and c.data["state"] is None
    s = (await ex.call("get_fleet_summary", {})).data
    assert s["vehicles"] == 3 and s["live_state_available"] is False


async def test_execute_replan_edge_cases(data, publisher):
    repos = fake_repos(data, 1)
    past = {"id": 1, "params": {"vins": [VIN_T1[0]], "depart_ts": "2020-01-01T00:00:00"}}
    data.agent_actions[0]["status"] = "APPROVED"
    res = await execute_replan(repos, publisher, past, approver="u")
    assert res.status == "FAILED" and "past" in res.errors[0] and publisher.events == []
    data.agent_actions[0]["status"] = "APPROVED"
    weird = {"id": 1, "params": {"vins": [FOREIGN_VIN, "bad"], "depart_ts": "not-a-date"}}
    res = await execute_replan(repos, publisher, weird, approver="u")
    assert res.status == "FAILED" and len(res.errors) == 2
    data.agent_actions[0]["status"] = "APPROVED"
    res = await execute_replan(repos, publisher, {"id": 1, "params": {}}, approver="u")
    assert res.status == "FAILED" and res.errors == ["action has no vehicles"]
    data.agent_actions[0]["status"] = "APPROVED"
    mixed = {"id": 1, "params": {"vins": [VALID_T1[0], FOREIGN_VIN], "target_soc": 90}}
    res = await execute_replan(repos, publisher, mixed, approver="u")
    assert res.status == "EXECUTED" and len(res.plan_ids) == 1 and len(res.errors) == 1
    assert publisher.events[-1]["target_soc"] == 90.0
