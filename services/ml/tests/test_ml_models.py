from __future__ import annotations

import json

import numpy as np
import pytest
from orbit_ml import models as m
from orbit_ml.data import Frame, synthetic_fleet


@pytest.fixture(scope="module")
def trained() -> m.TrainedModels:
    return m.train(synthetic_fleet(n_vehicles=3000, seed=7), seed=7)


def test_range_model_beats_baseline(trained):
    r = trained.metrics["range"]["synthetic_test"]
    assert r["model"]["mae_km"] < r["baseline"]["mae_km"] * 0.6
    assert r["model"]["mape_pct"] < r["baseline"]["mape_pct"]
    assert trained.metrics["range"]["beats_baseline"] is True
    assert trained.range_mae_km == r["model"]["mae_km"]


def test_soh_model_beats_baseline_on_recall(trained):
    s = trained.metrics["soh"]["synthetic_test"]
    assert s["positives"] > 5
    assert s["model"]["recall_lt80"] > s["baseline"]["recall_lt80"]
    assert s["model"]["recall_lt80"] > s["baseline_cycles_only"]["recall_lt80"]
    assert s["model"]["recall_lt80"] >= 0.8 and s["model"]["precision_lt80"] >= 0.6
    assert s["model"]["mae_pct"] < s["baseline"]["mae_pct"]
    assert trained.metrics["soh"]["beats_baseline_recall"] is True


def test_metrics_report_vehicle_split(trained):
    ds = trained.metrics["data_source"]
    assert ds["range_train"] == "synthetic" and ds["soh_train"] == "synthetic"
    assert "by vehicle" in ds["split"]


def test_guardrails(trained):
    f = synthetic_fleet(n_vehicles=50, seed=99)
    k = trained.predict_km_per_soc_pct(f)
    nominal = f["nominal_range_km"] / 100
    assert np.all(k >= 0.6 * nominal - 1e-9) and np.all(k <= 1.3 * nominal + 1e-9)
    assert np.allclose(trained.predict_range_km(f), f["soc_pct"] * k)
    f.cols["soh_pct"] = f["soh_pct"].copy()
    f.cols["soh_pct"][0] = np.nan
    s = trained.predict_soh_30d(f)
    assert np.isnan(s[0]) and np.all(s[1:] <= f["soh_pct"][1:])


def test_metric_helpers():
    assert m.mae(np.array([1.0, 3.0]), np.array([2.0, 2.0])) == 1.0
    assert np.isnan(m.mae(np.array([]), np.array([])))
    assert m.mape(np.array([100.0, 0.5]), np.array([90.0, 5.0])) == pytest.approx(10.0)  # tiny y excluded
    assert np.isnan(m.mape(np.array([0.1]), np.array([1.0])))
    assert m.recall_precision(np.array([70.0, 79.0, 90.0]), np.array([75.0, 85.0, 78.0])) == (0.5, 0.5, 2)
    r, p, n = m.recall_precision(np.array([90.0]), np.array([95.0]))
    assert np.isnan(r) and p == 0.0 and n == 0
    base = m.SohBaseline().fit(Frame({"km_per_day": np.array([0.0]), "soh_pct": [90.0], "y_soh_30d": [89.0]}))
    assert base.loss_per_km == 0.0


def test_live_rows_join_training_when_enough():
    syn = synthetic_fleet(n_vehicles=600, seed=4)
    live = synthetic_fleet(n_vehicles=500, snapshots=1, seed=5)
    live.cols["vin"] = np.array([f"LIVE{i:013d}" for i in range(500)])
    trained = m.train(syn, live, seed=4)
    assert trained.metrics["data_source"]["range_train"] == "synthetic+live"
    assert trained.metrics["range"]["live_test"]["n_rows"] > 0
    few = m.train(syn, live.take(np.arange(50)), seed=4)
    assert few.metrics["data_source"]["range_train"] == "synthetic"


def test_save_and_load(tmp_path, trained):
    copy = tmp_path / "docs" / "ml-metrics.json"
    path = m.save(trained, tmp_path / "artifacts", copy)
    assert path.exists() and json.loads(copy.read_text())["version"] == "v1"
    assert (tmp_path / "artifacts" / "metrics.json").read_text() == copy.read_text()
    loaded = m.load(tmp_path / "artifacts")
    f = synthetic_fleet(n_vehicles=20, seed=1)
    assert np.allclose(loaded.predict_km_per_soc_pct(f), trained.predict_km_per_soc_pct(f))
    with pytest.raises(FileNotFoundError):
        m.load(tmp_path / "nope")
    import joblib

    joblib.dump({"not": "models"}, tmp_path / "bad.joblib")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad.joblib").rename(tmp_path / "bad" / "models.joblib")
    with pytest.raises(TypeError):
        m.load(tmp_path / "bad")
    assert m._json_safe({"a": [float("nan"), 1.23456]}) == {"a": [None, 1.2346]}
