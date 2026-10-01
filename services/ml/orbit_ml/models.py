"""Range and SoH models, their baselines, evaluation on held-out vehicles, persistence.

* Range: target = km per 1 % SoC on the vehicle's next trips (so range_km = soc x prediction).
  Model = HistGradientBoostingRegressor (native NaN handling for vehicles that did not drive in the window).
  Baseline = nominal_range x soc / 100. Metrics on remaining range in km: MAE and MAPE.
* SoH: target = SoH in 30 days. Model = GradientBoostingRegressor behind a median imputer with missing-value
  indicators. Baseline = linear decline by distance: current SoH minus the fleet-median SoH loss per km
  (fitted on training vehicles) x expected km in 30 days. A second, weaker reference (linear regression on
  cycles and odometer only) is reported too. Metrics: MAE and recall/precision for "SoH < 80 within 30 days".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import make_pipeline

from .data import HORIZON_DAYS, RANGE_FEATURES, SOH_FEATURES, SOH_THRESHOLD, Frame, split_by_vehicle

MODEL_VERSION = "v1"
RANGE_METHOD = "hgb_v1"
SOH_METHOD = "model_v1"
KPSP_GUARD = (0.6, 1.3)  # prediction clipped to [0.6, 1.3] x nominal km per SoC % (guardrail)
MIN_LIVE_VEHICLES = 300  # live labelled vehicles needed before live rows join the training set
MIN_RANGE_KM_FOR_MAPE = 1.0


def mae(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean(np.abs(np.asarray(y) - np.asarray(p)))) if len(y) else float("nan")


def mape(y: np.ndarray, p: np.ndarray, floor: float = MIN_RANGE_KM_FOR_MAPE) -> float:
    y, p = np.asarray(y, float), np.asarray(p, float)
    m = np.abs(y) >= floor
    return float(np.mean(np.abs(y[m] - p[m]) / np.abs(y[m])) * 100) if m.any() else float("nan")


def recall_precision(
    y: np.ndarray, p: np.ndarray, threshold: float = SOH_THRESHOLD
) -> tuple[float, float, int]:
    actual, flagged = np.asarray(y) < threshold, np.asarray(p) < threshold
    tp = int((actual & flagged).sum())
    recall = tp / int(actual.sum()) if actual.any() else float("nan")
    precision = tp / int(flagged.sum()) if flagged.any() else 0.0
    return float(recall), float(precision), int(actual.sum())


def baseline_range_km(frame: Frame) -> np.ndarray:
    return np.asarray(frame["nominal_range_km"], float) * np.asarray(frame["soc_pct"], float) / 100.0


@dataclass
class SohBaseline:
    """Linear decline: SoH(t+30d) = SoH(t) - loss_per_km x km_per_day x 30, loss_per_km = fleet median."""

    loss_per_km: float = 0.0

    def fit(self, frame: Frame) -> SohBaseline:
        km = np.asarray(frame["km_per_day"], float) * HORIZON_DAYS
        loss = (np.asarray(frame["soh_pct"], float) - np.asarray(frame["y_soh_30d"], float)) / np.where(
            km > 0, km, np.nan
        )
        self.loss_per_km = float(np.nanmedian(loss)) if np.isfinite(loss).any() else 0.0
        return self

    def predict(self, frame: Frame) -> np.ndarray:
        km = np.nan_to_num(np.asarray(frame["km_per_day"], float), nan=0.0) * HORIZON_DAYS
        return np.asarray(frame["soh_pct"], float) - self.loss_per_km * km


@dataclass
class TrainedModels:
    range_model: Any
    soh_model: Any
    soh_baseline: SohBaseline
    cycles_baseline: Any
    trained_at: str
    range_mae_km: float
    metrics: dict[str, Any] = field(default_factory=dict)

    def predict_km_per_soc_pct(self, frame: Frame) -> np.ndarray:
        nominal = np.asarray(frame["nominal_range_km"], float) / 100.0
        pred = self.range_model.predict(frame.matrix(RANGE_FEATURES))
        return np.clip(pred, KPSP_GUARD[0] * nominal, KPSP_GUARD[1] * nominal)

    def predict_range_km(self, frame: Frame) -> np.ndarray:
        return np.asarray(frame["soc_pct"], float) * self.predict_km_per_soc_pct(frame)

    def predict_soh_30d(self, frame: Frame) -> np.ndarray:
        pred = self.soh_model.predict(frame.matrix(SOH_FEATURES))
        now = np.asarray(frame["soh_pct"], float)
        # guardrail: SoH never recovers; NaN current SoH -> NaN forecast
        return np.where(np.isfinite(now), np.clip(pred, 0.0, np.nan_to_num(now, nan=100.0)), np.nan)


def fit_range(train: Frame, seed: int = 7) -> HistGradientBoostingRegressor:
    m = HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.08, l2_regularization=0.1, random_state=seed
    )
    m.fit(train.matrix(RANGE_FEATURES), np.asarray(train["y_km_per_soc_pct"], float))
    return m


def fit_soh(train: Frame, seed: int = 7) -> Any:
    m = make_pipeline(
        SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
        GradientBoostingRegressor(
            n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.8, random_state=seed
        ),
    )
    m.fit(train.matrix(SOH_FEATURES), np.asarray(train["y_soh_30d"], float))
    return m


def fit_cycles_baseline(train: Frame) -> LinearRegression:
    return LinearRegression().fit(train.matrix(("cycles", "odo_km")), np.asarray(train["y_soh_30d"], float))


def range_metrics(models: TrainedModels, test: Frame) -> dict[str, Any]:
    y = np.asarray(test["soc_pct"], float) * np.asarray(test["y_km_per_soc_pct"], float)
    pred, base = models.predict_range_km(test), baseline_range_km(test)
    return {
        "n_rows": len(test),
        "n_vehicles": len(set(map(str, test["vin"]))),
        "model": {"mae_km": mae(y, pred), "mape_pct": mape(y, pred)},
        "baseline": {"mae_km": mae(y, base), "mape_pct": mape(y, base)},
    }


def soh_metrics(models: TrainedModels, test: Frame) -> dict[str, Any]:
    y = np.asarray(test["y_soh_30d"], float)
    out: dict[str, Any] = {"n_rows": len(test), "n_vehicles": len(set(map(str, test["vin"])))}
    preds = {
        "model": models.predict_soh_30d(test),
        "baseline": models.soh_baseline.predict(test),
        "baseline_cycles_only": models.cycles_baseline.predict(test.matrix(("cycles", "odo_km"))),
    }
    for name, p in preds.items():
        r, prec, pos = recall_precision(y, p)
        out[name] = {"mae_pct": mae(y, p), "recall_lt80": r, "precision_lt80": prec}
        out["positives"] = pos
    return out


def train(synthetic: Frame, live_range: Frame | None = None, seed: int = 7) -> TrainedModels:
    """Fit both models on training vehicles, evaluate on held-out vehicles, return models + metrics."""
    syn_train, syn_test = split_by_vehicle(synthetic)
    live_train, live_test = split_by_vehicle(live_range) if live_range is not None else (Frame(), Frame())
    use_live = len(live_train) >= MIN_LIVE_VEHICLES
    range_train = Frame.concat([syn_train, live_train]) if use_live else syn_train

    models = TrainedModels(
        range_model=fit_range(range_train, seed),
        soh_model=fit_soh(syn_train, seed),
        soh_baseline=SohBaseline().fit(syn_train),
        cycles_baseline=fit_cycles_baseline(syn_train),
        trained_at=datetime.now(UTC).isoformat(timespec="seconds"),
        range_mae_km=0.0,
    )
    rm = range_metrics(models, syn_test)
    sm = soh_metrics(models, syn_test)
    models.range_mae_km = rm["model"]["mae_km"]
    live_n = len(live_train) + len(live_test)
    metrics: dict[str, Any] = {
        "version": MODEL_VERSION,
        "trained_at": models.trained_at,
        "seed": seed,
        "data_source": {
            "range_train": "synthetic+live" if use_live else "synthetic",
            "soh_train": "synthetic",
            "synthetic_rows": len(synthetic),
            "synthetic_vehicles": len(set(map(str, synthetic["vin"]))),
            "live_labelled_vehicles": live_n,
            "live_min_vehicles_to_train": MIN_LIVE_VEHICLES,
            "split": "sha256(vin) % 100 < 20 -> test (by vehicle, stable across runs)",
        },
        "range": {"synthetic_test": rm},
        "soh": {"synthetic_test": sm},
    }
    if len(live_test):
        metrics["range"]["live_test"] = range_metrics(models, live_test)
    metrics["range"]["beats_baseline"] = bool(rm["model"]["mae_km"] < rm["baseline"]["mae_km"])
    metrics["soh"]["beats_baseline_recall"] = bool(
        sm["model"]["recall_lt80"] > sm["baseline"]["recall_lt80"]
        and sm["model"]["recall_lt80"] > sm["baseline_cycles_only"]["recall_lt80"]
    )
    models.metrics = metrics
    return models


# --- persistence ---

MODEL_FILE = "models.joblib"
METRICS_FILE = "metrics.json"


def _json_safe(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_json_safe(v) for v in x]
    if isinstance(x, float):
        return round(x, 4) if np.isfinite(x) else None
    return x


def save(models: TrainedModels, artifacts: Path, metrics_copy: Path | None = None) -> Path:
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / MODEL_FILE
    joblib.dump(models, path)
    text = json.dumps(_json_safe(models.metrics), indent=2, sort_keys=True) + "\n"
    (artifacts / METRICS_FILE).write_text(text)
    if metrics_copy is not None:
        metrics_copy.parent.mkdir(parents=True, exist_ok=True)
        metrics_copy.write_text(text)
    return path


def load(artifacts: Path) -> TrainedModels:
    path = artifacts / MODEL_FILE
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `python -m orbit_ml train` first")
    obj = joblib.load(path)  # artifacts are produced by this package only, never user-supplied
    if not isinstance(obj, TrainedModels):
        raise TypeError(f"{path} does not contain orbit_ml TrainedModels")
    return obj
