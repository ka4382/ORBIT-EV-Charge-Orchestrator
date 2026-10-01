from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pytest
from ml_fakes import StubConn, agg_row
from orbit_ml.data import (
    CATALOGUE,
    RANGE_FEATURES,
    SOC_HIST,
    SOH_FEATURES,
    Frame,
    LeakageError,
    assert_no_leakage,
    distance_weighted_factor,
    features_from_rows,
    is_test_vehicle,
    load_live,
    load_live_range_labels,
    speed_factor,
    split_by_vehicle,
    synthetic_fleet,
)

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def test_speed_factor_matches_simulator():
    # simulator: speedFactor := 0.8 + 0.5*(SpeedKmh/road.MaxSpeedKmh), MaxSpeedKmh = 90
    assert speed_factor(np.array([0.0, 45.0, 90.0])).tolist() == pytest.approx([0.8, 1.05, 1.3])
    # distance weighting: a parked sample contributes no km
    assert distance_weighted_factor(np.array([[0.0, 90.0]]))[0] == pytest.approx(1.3)
    assert distance_weighted_factor(np.array([[0.0, 0.0]]))[0] == 1.0


def test_synthetic_fleet_shapes_and_physics():
    f = synthetic_fleet(n_vehicles=400, snapshots=2, seed=3)
    assert len(f) == 800 and len(set(f["vin"])) == 400
    assert all(len(v) == 17 for v in f["vin"])
    for c in (*RANGE_FEATURES, *SOH_FEATURES, *SOC_HIST, "y_km_per_soc_pct", "y_soh_30d"):
        assert c in f and len(f[c]) == 800
    assert np.allclose(sum(f[c] for c in SOC_HIST), 1.0)
    # label never exceeds what the slowest possible driving (factor 0.8) allows, never below the fastest (1.3)
    nominal = f["nominal_range_km"] / 100
    assert np.all(f["y_km_per_soc_pct"] <= nominal / 0.8 + 1e-9)
    assert np.all(f["y_km_per_soc_pct"] >= nominal / 1.3 - 1e-9)
    assert np.all(f["y_soh_30d"] <= f["soh_pct"] + 0.2)  # SoH never recovers (0.2 = reading noise)
    assert set(f["battery_kwh"]) <= {c[1] for c in CATALOGUE}
    # some vehicles did not drive in the window: efficiency is missing, like a parked live vehicle
    assert 0 < np.isnan(f["obs_kwh_per_km"]).mean() < 0.6


def test_synthetic_fast_cohort_degrades_faster():
    f = synthetic_fleet(n_vehicles=3000, snapshots=1, seed=11)
    loss = f["soh_pct"] - f["y_soh_30d"]
    assert 0.005 < f["fast_degrade"].mean() < 0.04
    assert np.median(loss[f["fast_degrade"]]) > 10 * np.median(loss[~f["fast_degrade"]])


def test_synthetic_is_deterministic():
    a, b = synthetic_fleet(n_vehicles=50, seed=5), synthetic_fleet(n_vehicles=50, seed=5)
    assert np.array_equal(a["y_soh_30d"], b["y_soh_30d"]) and list(a["vin"]) == list(b["vin"])


def test_split_is_by_vehicle_and_stable():
    f = synthetic_fleet(n_vehicles=1000, snapshots=3, seed=1)
    train, test = split_by_vehicle(f)
    tr, te = set(train["vin"]), set(test["vin"])
    assert not tr & te, "a vehicle on both sides leaks its own degradation curve"
    assert len(train) + len(test) == len(f) and 0.12 < len(te) / 1000 < 0.28
    # every snapshot of a test vehicle is in test
    assert all(v in te for v in test["vin"]) and sum(1 for v in f["vin"] if v in te) == len(test)
    # stable across runs and datasets: same VIN -> same side
    assert all(is_test_vehicle(v) for v in te) and not any(is_test_vehicle(v) for v in tr)


def test_leakage_check_detects_overlap():
    f = synthetic_fleet(n_vehicles=20, snapshots=2, seed=2)
    with pytest.raises(LeakageError):
        assert_no_leakage(f.take(np.array([0, 1, 2])), f.take(np.array([1, 5])))  # rows 0,1 = same vehicle
    assert_no_leakage(f, Frame())  # empty side is fine


def test_frame_helpers():
    f = Frame({"a": np.array([1.0, 2.0]), "b": np.array([3.0, 4.0])})
    assert len(f) == 2 and "a" in f and len(Frame()) == 0
    assert f.matrix(["b", "a"]).tolist() == [[3.0, 1.0], [4.0, 2.0]]
    g = Frame.concat([f, Frame({"a": np.array([9.0])}), Frame()])
    assert g["a"].tolist() == [1.0, 2.0, 9.0] and "b" not in g
    assert len(Frame.concat([])) == 0


def test_features_from_rows():
    rows = [
        agg_row("VIN00000000000001", 1),
        agg_row("VIN00000000000002", 2, km_driving=0.5, kwh_charged=0.0, kwh_dc=0.0, hours=None),
        agg_row("VIN00000000000003", 3, soc_pct=None),  # ICE-like: dropped
    ]
    hist = [
        {"vin": "VIN00000000000001", "bin": 5, "n": 3},
        {"vin": "VIN00000000000001 ", "bin": 9, "n": 1},
    ]
    f = features_from_rows(rows, hist)
    assert list(f["vin"]) == ["VIN00000000000001", "VIN00000000000002"]
    # 12 % SoC of 60 kWh over 36 km = 0.2 kWh/km
    assert f["obs_kwh_per_km"][0] == pytest.approx(0.2)
    assert math.isnan(f["obs_kwh_per_km"][1])  # drove < 2 km: unknown, not zero
    # SoH fell 0.008 % over 20 kWh charged -> 0.04 % per 100 kWh
    assert f["soh_slope_per_100kwh"][0] == pytest.approx(0.04)
    assert math.isnan(f["soh_slope_per_100kwh"][1]) and math.isnan(f["dc_kwh_share"][1])
    assert f["dc_kwh_share"][0] == pytest.approx(0.25)
    assert f["km_per_day"][0] == pytest.approx(160.0) and math.isnan(f["km_per_day"][1])
    assert f["cycles"][0] == pytest.approx(100.0)
    assert f["soc_hist_5"][0] == pytest.approx(0.75) and f["soc_hist_9"][0] == pytest.approx(0.25)
    assert np.isnan(f["soc_hist_0"][1])  # no 5-minute buckets yet
    assert f["vehicle_id"].dtype == np.int64


def test_load_live_uses_window():
    conn = StubConn(agg=[agg_row("VIN00000000000001", 1)])
    f = load_live(conn, until=NOW, hours=4)
    assert len(f) == 1
    p = conn.params[0]
    assert p["until"] == NOW and (p["until"] - p["since"]).total_seconds() == 4 * 3600
    assert "telemetry" in conn.statements[0] and "telemetry_5m" in conn.statements[1]


def test_live_range_labels_half_window():
    first = [
        agg_row("VIN00000000000001", 1),
        agg_row("VIN00000000000002", 2),
        agg_row("VIN00000000000003", 3),
    ]
    second = [
        agg_row("VIN00000000000001", 1, km_driving=30.0, soc_drop_driving=10.0),
        agg_row("VIN00000000000002", 2, km_driving=1.0, soc_drop_driving=0.2),  # did not drive enough
    ]
    conn = StubConn(agg=first, agg_second=second)
    f = load_live_range_labels(conn, until=NOW, hours=6)
    assert list(f["vin"]) == ["VIN00000000000001"]
    assert f["y_km_per_soc_pct"].tolist() == [3.0]
    halves = [p for p, s in zip(conn.params, conn.statements, strict=True) if "FROM telemetry\n" in s]
    assert halves[0]["until"] == halves[1]["since"]  # features strictly before labels
