from datetime import timedelta

import orjson
from orbit_state.domain.state import (
    LastSeen,
    derive_status,
    is_stale,
    last_seen_from_value,
    state_message,
    state_value,
    vehicle_state,
)
from orbit_state.domain.summary import FleetTracker, summary_hash, summary_message
from orbit_state.domain.throttle import PublishThrottle
from state_fakes import NOW, VIN, VIN2, ev


def test_derive_status_matrix():
    assert derive_status("AC", 0.0, 50.0) == "CHARGING"
    assert derive_status("DC", 90.0, 10.0) == "CHARGING"  # charging beats at-risk
    assert derive_status("NONE", 90.0, 10.0) == "AT_RISK"
    assert derive_status("NONE", 14.9, 14.9) == "AT_RISK"
    assert derive_status("NONE", 2.1, 15.0) == "DRIVING"
    assert derive_status("NONE", 2.0, 15.0) == "IDLE"
    assert derive_status("NONE", 0.0, None) == "IDLE"


def test_is_stale_ordering():
    last = LastSeen(seq=10, ts=NOW)
    assert not is_stale(ev(seq=11, dt_s=1), None)
    assert is_stale(ev(seq=9, dt_s=-1), last)  # out of order
    assert is_stale(ev(seq=10, dt_s=0), last)  # exact duplicate
    assert not is_stale(ev(seq=11, dt_s=0), last)  # same ts, newer seq
    assert not is_stale(ev(seq=1, dt_s=5), last)  # seq reset but newer clock -> accept


def test_vehicle_state_and_redis_value():
    st = vehicle_state(ev(seq=5, speed_kmh=30.0, dtc=["P0301"], soh_pct=None))
    assert st["status"] == "DRIVING" and st["ts"] == "2026-09-25T10:15:02Z" and st["dtc"] == ["P0301"]
    doc = orjson.loads(state_value(st))
    assert doc["dtc"] == ["P0301"] and doc["seq"] == 5 and doc["soh_pct"] is None and doc["soc_pct"] == 41.0
    seen = last_seen_from_value(state_value(st))
    assert seen == LastSeen(5, NOW)
    assert last_seen_from_value(None) is None and last_seen_from_value(b'{"seq": "x"}') is None
    msg = orjson.loads(state_message(st))
    assert msg["type"] == "state" and msg["data"]["vin"] == VIN


def test_throttle_allows_once_per_interval():
    t = PublishThrottle(2.0)
    assert t.allow("a", 100.0)
    assert not t.allow("a", 101.9)
    assert t.allow("a", 102.0)
    assert t.allow("b", 102.0)
    assert len(t) == 2
    t.forget("a")
    assert t.allow("a", 102.1)


def test_fleet_tracker_summary_and_kwh():
    tr = FleetTracker(online_window_s=120)
    tr.observe(ev(seq=1, charging_state="AC", charging_kw=7.2))
    tr.observe(ev(seq=2, dt_s=10, charging_state="AC", charging_kw=7.2))  # 10 s at 7.2 kW = 0.02 kWh
    tr.observe(ev(seq=1, vin=VIN2, soc_pct=10.0, dt_s=60))  # at risk, reporting
    s = tr.summary(12, NOW + timedelta(seconds=65))
    assert s["vehicles"] == 2 and s["online"] == 2 and s["charging"] == 1 and s["at_risk"] == 1
    assert s["avg_soc"] == 25.5 and s["soc_n"] == 2
    assert abs(s["kwh_today"] - (7.2 / 3600 + 7.2 * 10 / 3600)) < 1e-6
    assert tr.tenants() == [12]
    assert tr.summary(99)["vehicles"] == 0 and tr.summary(99)["avg_soc"] is None
    # a vehicle silent for longer than the online window is forgotten (e.g. its partition moved away)
    later = tr.summary(12, NOW + timedelta(seconds=10 + 121))
    assert later["vehicles"] == 1 and later["online"] == 1 and later["at_risk"] == 1
    h = summary_hash(s)
    assert h["avg_soc"] == "25.5" and summary_hash(tr.summary(99))["avg_soc"] == ""
    assert orjson.loads(summary_message(s))["type"] == "summary"


def test_fleet_tracker_caps_gap_and_resets_per_day():
    tr = FleetTracker()
    tr.observe(ev(seq=1, charging_state="DC", charging_kw=100.0))
    tr.observe(ev(seq=2, dt_s=3600, charging_state="NONE"))  # hour gap capped to 60 s
    assert abs(tr.summary(12)["kwh_today"] - (100 / 3600 + 100 * 60 / 3600)) < 1e-3
    tr.observe(ev(seq=3, dt_s=86400 * 2, charging_state="AC", charging_kw=1.0))
    assert tr.summary(12)["kwh_today"] == 0.0  # new day -> reset (prev charging_kw was 0)
