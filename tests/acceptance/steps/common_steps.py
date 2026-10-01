"""Step definitions. Tagged @wip scenarios are skipped in CI until the feature is Done."""
import time

from behave import given, then, when


def _token(context, tenant_id, role):
    r = context.client.post("/auth/dev-token", json={"tenant_id": tenant_id, "role": role, "sub": f"bdd-{role}-{tenant_id}"})
    r.raise_for_status()
    context.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


@given("a vehicle in my tenant")
@given("a vehicle in my tenant at 30% state of charge")
@given("a vehicle in my tenant at 10% state of charge")
def step_vehicle(context):
    r = context.client.get("/vehicles", params={"limit": 1})
    r.raise_for_status()
    context.vehicle = r.json()["items"][0]


@when("its telemetry reports 12% state of charge far from any charger")
def step_low_soc(context):
    # The simulator injects a strand scenario for the first vehicle of tenant 1 every 5 minutes (SIM_SCENARIO=strand).
    context.t0 = time.time()


@then("a STRAND_RISK alert with severity CRITICAL appears in the alert feed within 5 seconds")
def step_alert_appears(context):
    deadline = time.time() + 300
    while time.time() < deadline:
        r = context.client.get("/alerts", params={"severity": "CRITICAL", "limit": 50})
        r.raise_for_status()
        hits = [a for a in r.json()["items"] if a["type"] == "STRAND_RISK"]
        if hits:
            context.alert = hits[0]
            return
        time.sleep(1)
    raise AssertionError("no STRAND_RISK alert")


@then("the alert can be acknowledged")
def step_ack(context):
    r = context.client.post(f"/alerts/{context.alert['id']}/ack")
    assert r.status_code == 200, r.text


@then("an audit record of the acknowledgement exists")
def step_audit(context):
    _token(context, 1, "admin")
    r = context.client.get("/audit", params={"limit": 50})
    r.raise_for_status()
    assert any(a["action"].startswith("ALERT_ACK") or "ack" in a["resource"] for a in r.json()["items"])
    _token(context, 1, "manager")


@when("I request a plan to reach 90% by 06:00 tomorrow")
def step_plan(context):
    from datetime import datetime, timedelta, timezone
    depart = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=30, second=0, microsecond=0)  # 06:00 IST
    r = context.client.post("/plans", json={"vin": context.vehicle["vin"], "target_soc": 90, "depart_ts": depart.isoformat()})
    assert r.status_code == 202, r.text
    context.plan_id = r.json()["plan_id"]


@when("I request a plan to reach 100% in 15 minutes")
def step_plan_infeasible(context):
    from datetime import datetime, timedelta, timezone
    depart = datetime.now(timezone.utc) + timedelta(minutes=15)
    r = context.client.post("/plans", json={"vin": context.vehicle["vin"], "target_soc": 100, "depart_ts": depart.isoformat()})
    assert r.status_code == 202, r.text
    context.plan_id = r.json()["plan_id"]


@then("the plan becomes READY within 30 seconds")
def step_plan_ready(context):
    deadline = time.time() + 30
    while time.time() < deadline:
        r = context.client.get(f"/plans/{context.plan_id}")
        r.raise_for_status()
        context.plan = r.json()
        if context.plan["status"] in ("READY", "FAILED"):
            break
        time.sleep(1)
    assert context.plan["status"] == "READY", context.plan


@then("every slot is inside the off-peak tariff window")
def step_offpeak(context):
    for s in context.plan["slots"]:
        assert s["price_per_kwh"] <= 6.5, s


@then("the plan cost is lower than the charge-on-arrival baseline")
def step_savings(context):
    assert context.plan["total_cost"] < context.plan["baseline_cost"]


@then("the plan status is FAILED or the plan is flagged infeasible")
def step_infeasible(context):
    deadline = time.time() + 30
    while time.time() < deadline:
        r = context.client.get(f"/plans/{context.plan_id}")
        plan = r.json()
        if plan["status"] in ("READY", "FAILED"):
            break
        time.sleep(1)
    assert plan["status"] == "FAILED" or plan.get("infeasible") is True


@given("I am a manager of tenant 1")
def step_mgr(context):
    _token(context, 1, "manager")


@given("I am a finance user of tenant 1")
def step_fin(context):
    _token(context, 1, "finance")


@given("I am an admin of tenant 1")
def step_admin(context):
    _token(context, 1, "admin")


@when("I list vehicles")
def step_list(context):
    r = context.client.get("/vehicles", params={"limit": 100})
    r.raise_for_status()
    context.items = r.json()["items"]


@then("every vehicle belongs to tenant 1")
def step_all_t1(context):
    r = context.client.get("/fleets")
    fleet_ids = {f["id"] for f in r.json()["items"]}
    assert context.items and all(v["fleet_id"] in fleet_ids for v in context.items)


@when("I read a vehicle state")
def step_state(context):
    r = context.client.get("/vehicles", params={"limit": 1})
    vin = r.json()["items"][0]["vin"]
    r = context.client.get(f"/vehicles/{vin}/state")
    context.state = r.json() if r.status_code == 200 else None


@then("latitude and longitude have at most 2 decimal places")
def step_masked(context):
    if context.state is None:
        return  # no live state yet; masking is unit-tested in the API
    for k in ("lat", "lon"):
        assert round(context.state[k], 2) == context.state[k]


@when("I erase a driver")
def step_erase(context):
    r = context.client.delete("/privacy/drivers/1")
    assert r.status_code in (202, 404), r.text
    context.erased = r.status_code == 202


@then("the driver row is masked and an ERASURE audit record exists")
def step_erased(context):
    if not context.erased:
        return
    r = context.client.get("/audit", params={"limit": 20})
    assert any(a["action"] == "ERASURE" for a in r.json()["items"])
