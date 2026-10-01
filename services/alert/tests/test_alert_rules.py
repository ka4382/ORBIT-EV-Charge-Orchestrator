from alert_fakes import NOW, VIN, ev
from orbit_alert.domain.alerts import (
    BATTERY_DTC,
    CHARGE_INTERRUPTED,
    CRITICAL,
    LOW_SOC,
    SOC_DROP,
    SOH_DROP,
    STRAND_RISK,
    WARN,
    Alert,
)
from orbit_alert.domain.rules import (
    RuleContext,
    evaluate,
    remaining_range_km,
    rule_battery_dtc,
    rule_charge_interrupted,
    rule_low_soc,
    rule_soc_drop,
    rule_soh_drop,
    rule_strand_risk,
)


def test_low_soc_fires_below_20_only():
    assert rule_low_soc(ev(soc_pct=19.9), RuleContext()).severity == WARN
    assert rule_low_soc(ev(soc_pct=20.0), RuleContext()) is None
    assert rule_low_soc(ev(soc_pct=None), RuleContext()) is None


def test_strand_risk_requires_low_soc_and_no_reachable_charger():
    ctx = RuleContext(nominal_range_km=300.0, reachable_charger=False)
    a = rule_strand_risk(ev(soc_pct=10.0), ctx)
    assert a is not None and a.severity == CRITICAL and a.type == STRAND_RISK
    assert a.payload["remaining_range_km"] == 30.0 and a.payload["lat"] == 13.08
    assert rule_strand_risk(ev(soc_pct=10.0), RuleContext(reachable_charger=True)) is None
    assert rule_strand_risk(ev(soc_pct=10.0), RuleContext(reachable_charger=None)) is None
    assert rule_strand_risk(ev(soc_pct=15.0), ctx) is None
    assert rule_strand_risk(ev(soc_pct=None), ctx) is None
    assert remaining_range_km(None, 300.0) is None and remaining_range_km(50.0, None) is None
    assert remaining_range_km(50.0, 300.0) == 150.0


def test_soc_drop_over_40_points():
    a = rule_soc_drop(ev(soc_pct=30.0), RuleContext(max_soc_2h=75.0))
    assert a is not None and a.type == SOC_DROP and a.payload["drop_pts"] == 45.0
    assert rule_soc_drop(ev(soc_pct=35.0), RuleContext(max_soc_2h=75.0)) is None
    assert rule_soc_drop(ev(soc_pct=35.0), RuleContext(max_soc_2h=None)) is None
    assert rule_soc_drop(ev(soc_pct=None), RuleContext(max_soc_2h=90.0)) is None


def test_soh_drop_below_80():
    assert rule_soh_drop(ev(soh_pct=79.9), RuleContext()).type == SOH_DROP
    assert rule_soh_drop(ev(soh_pct=80.0), RuleContext()) is None
    assert rule_soh_drop(ev(soh_pct=None), RuleContext()) is None


def test_battery_dtc_only_for_battery_codes():
    a = rule_battery_dtc(ev(dtc=["P0301", "P0A80"]), RuleContext())
    assert a is not None and a.type == BATTERY_DTC and a.payload["codes"] == ["P0A80"]
    assert "battery" in a.payload["descriptions"]["P0A80"].lower()
    assert rule_battery_dtc(ev(dtc=["P0301"]), RuleContext()) is None
    assert rule_battery_dtc(ev(dtc=[]), RuleContext()) is None


def test_charge_interrupted_conditions():
    prev = RuleContext(prev_charging_state="DC", prev_charging_kw=50.0, prev_ts_epoch=NOW.timestamp())
    a = rule_charge_interrupted(ev(dt_s=30, soc_pct=60.0, charging_state="NONE"), prev)
    assert a is not None and a.type == CHARGE_INTERRUPTED and a.payload["gap_s"] == 30.0
    assert rule_charge_interrupted(ev(dt_s=30, soc_pct=85.0), prev) is None  # soc >= 80
    assert rule_charge_interrupted(ev(dt_s=90, soc_pct=60.0), prev) is None  # gap > 60 s
    assert rule_charge_interrupted(ev(dt_s=-5, soc_pct=60.0), prev) is None  # out-of-order (negative gap)
    assert (
        rule_charge_interrupted(ev(dt_s=30, soc_pct=60.0, charging_state="AC"), prev) is None
    )  # still charging
    assert rule_charge_interrupted(ev(dt_s=30, soc_pct=60.0), RuleContext(prev_charging_state="NONE")) is None
    no_kw = RuleContext(prev_charging_state="AC", prev_charging_kw=0.0, prev_ts_epoch=NOW.timestamp())
    assert rule_charge_interrupted(ev(dt_s=30, soc_pct=60.0), no_kw) is None
    no_ts = RuleContext(prev_charging_state="AC", prev_charging_kw=5.0, prev_ts_epoch=None)
    assert rule_charge_interrupted(ev(dt_s=30, soc_pct=60.0), no_ts) is None
    assert rule_charge_interrupted(ev(dt_s=30, soc_pct=None), prev) is None


def test_evaluate_runs_all_rules_in_order():
    ctx = RuleContext(
        nominal_range_km=300.0,
        reachable_charger=False,
        max_soc_2h=80.0,
        prev_charging_state="AC",
        prev_charging_kw=7.0,
        prev_ts_epoch=NOW.timestamp() - 10,
    )
    alerts = evaluate(ev(soc_pct=10.0, soh_pct=70.0, dtc=["P0AFA"]), ctx)
    assert [a.type for a in alerts] == [
        STRAND_RISK,
        LOW_SOC,
        SOC_DROP,
        SOH_DROP,
        BATTERY_DTC,
        CHARGE_INTERRUPTED,
    ]
    assert evaluate(ev(), RuleContext()) == []


def test_alert_serialisation():
    a = Alert(12, VIN, LOW_SOC, WARN, NOW, {"soc_pct": 10.0}, id=7)
    d = a.to_dict()
    assert d["ts"] == "2026-09-25T10:15:00Z" and d["id"] == 7 and d["acked_by"] is None
    assert a.kafka_key == b"12:" + VIN.encode() and a.dedupe_key == f"alert:dedupe:{VIN}:LOW_SOC"
    assert b'"type":"alert"' in a.ws_message() and b'"vin"' in a.to_json()
