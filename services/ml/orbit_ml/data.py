"""Dataset builder: per-vehicle feature frames from live Postgres telemetry or from a synthetic generator that
mirrors the simulator physics (simulator/internal/fleet/vehicle.go, simulator/internal/road).

Two sources, never blended silently:

* **synthetic** — `synthetic_fleet()` draws vehicles with the simulator's catalogue, SoC drain law
  (kWh/km = capacity/range x speed factor 0.8 + 0.5 v/90), SoH loss per kWh charged (0.0004 %/kWh, 0.008 %/kWh
  for the 2 % fast-degrade cohort) and initial SoH/odometer distributions. Labels (future km per SoC %, SoH in
  30 days) come from the same law, so it is the only source of SoH labels.
* **live** — `load_live()` aggregates raw `telemetry` (speed profile, SoC-drop-per-km efficiency,
  kWh charged, DC share, ambient temperature, SoH slope) and `telemetry_5m` (SoC histogram) per vehicle
  for the last N hours, joined to `vehicle_model`. `load_live_range_labels()` turns the window into
  (first half -> features, second half -> realised km per SoC %) rows, used for training/evaluation only
  when enough vehicles drove.

Splits are always by VEHICLE (stable hash of the VIN), never by time, so no vehicle is on both sides.
"""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

import numpy as np

# --- simulator constants (keep in sync with simulator/internal/fleet/vehicle.go and road.go) ---
MAX_SPEED_KMH = 90.0
SOH_NORMAL_PER_KWH = 0.0004
SOH_FAST_PER_KWH = 0.008
FAST_DEGRADE_SHARE = 0.02
SOH_THRESHOLD = 80.0
HORIZON_DAYS = 30
# (name, capacity kWh, nominal range km) — battery models from orbit_common.seed.MODELS (ICE has no battery)
CATALOGUE: tuple[tuple[str, float, float], ...] = (
    ("A-Van 60", 60.0, 320.0),
    ("A-Sedan 45", 45.0, 300.0),
    ("A-Hybrid", 14.0, 60.0),
    ("B-Truck 120", 120.0, 380.0),
    ("B-Hatch 30", 30.0, 220.0),
)
SOC_BINS = 10

SOC_HIST = tuple(f"soc_hist_{i}" for i in range(SOC_BINS))
RANGE_FEATURES = (
    "battery_kwh",
    "nominal_range_km",
    "mean_speed_kmh",
    "p90_speed_kmh",
    "moving_frac",
    "ambient_c_mean",
    "soh_pct",
    "obs_kwh_per_km",
)
SOH_FEATURES = (
    "soh_pct",
    "soh_slope_per_100kwh",
    "km_per_day",
    "dc_kwh_share",
    "ambient_c_mean",
    "cycles",
    "odo_km",
    "battery_kwh",
    "nominal_range_km",
)


def speed_factor(speed_kmh: np.ndarray) -> np.ndarray:
    """Simulator consumption multiplier: 0.8 + 0.5 * v / MaxSpeedKmh."""
    return 0.8 + 0.5 * (np.asarray(speed_kmh, dtype=float) / MAX_SPEED_KMH)


def distance_weighted_factor(speeds: np.ndarray) -> np.ndarray:
    """Energy per km over a speed trace = sum(v f(v)) / sum(v) (km per sample is proportional to v)."""
    s = np.asarray(speeds, dtype=float)
    num = (s * speed_factor(s)).sum(axis=-1)
    den = s.sum(axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / np.where(den > 0, den, 1.0), 1.0)


@dataclass
class Frame:
    """Column store: name -> 1-D numpy array, all of equal length. One row per vehicle (or snapshot)."""

    cols: dict[str, np.ndarray] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(next(iter(self.cols.values()))) if self.cols else 0

    def __getitem__(self, name: str) -> np.ndarray:
        return self.cols[name]

    def __contains__(self, name: object) -> bool:
        return name in self.cols

    def take(self, idx: np.ndarray) -> Frame:
        return Frame({k: v[idx] for k, v in self.cols.items()})

    def matrix(self, names: Sequence[str]) -> np.ndarray:
        return np.column_stack([np.asarray(self.cols[n], dtype=float) for n in names])

    @staticmethod
    def concat(frames: Sequence[Frame]) -> Frame:
        frames = [f for f in frames if len(f)]
        if not frames:
            return Frame()
        keys = [k for k in frames[0].cols if all(k in f.cols for f in frames)]
        return Frame({k: np.concatenate([f.cols[k] for f in frames]) for k in keys})


# --- vehicle split ------------------------------------------------------------------------------------------


def is_test_vehicle(vin: str, test_pct: int = 20, salt: str = "orbit-ml-v1") -> bool:
    """Stable hash split: a VIN is in the test set in every run, for every dataset built from it."""
    h = hashlib.sha256(f"{salt}:{vin}".encode()).digest()
    return int.from_bytes(h[:4], "big") % 100 < test_pct


def split_by_vehicle(frame: Frame, test_pct: int = 20) -> tuple[Frame, Frame]:
    test = np.array([is_test_vehicle(str(v), test_pct) for v in frame["vin"]], dtype=bool)
    train, held = frame.take(np.flatnonzero(~test)), frame.take(np.flatnonzero(test))
    assert_no_leakage(train, held)
    return train, held


class LeakageError(AssertionError):
    pass


def assert_no_leakage(train: Frame, test: Frame) -> None:
    """Raise if any vehicle has rows on both sides (snapshots of one vehicle are highly correlated)."""
    if not len(train) or not len(test):
        return
    shared = set(map(str, train["vin"])) & set(map(str, test["vin"]))
    if shared:
        raise LeakageError(f"{len(shared)} vehicles in both train and test, e.g. {sorted(shared)[:3]}")


# --- synthetic generator ------------------------------------------------------------------------------------


def synthetic_fleet(
    n_vehicles: int = 6000, snapshots: int = 3, seed: int = 7, fast_share: float = FAST_DEGRADE_SHARE
) -> Frame:
    """Synthetic per-vehicle snapshots with labels, mirroring the simulator.

    Additions over the simulator, all documented in docs/ml.md: a per-vehicle duty cycle (mean cruise speed;
    the simulator draws every trip from the same distribution), observation noise on the measured efficiency
    and SoH slope, and missing values where a vehicle did not drive or charge inside the observation window.
    """
    rng = np.random.default_rng(seed)
    nv = n_vehicles
    model = rng.integers(0, len(CATALOGUE), nv)
    cap = np.array([CATALOGUE[m][1] for m in model])
    rng_km = np.array([CATALOGUE[m][2] for m in model])
    fast = rng.random(nv) < fast_share
    soh0 = np.where(fast, 88 + rng.random(nv) * 6, 92 + rng.random(nv) * 8)
    odo0 = rng.random(nv) * 150_000
    duty = np.clip(rng.normal(47.0, 9.0, nv), 25.0, 75.0)  # mean cruise speed of this vehicle's routes
    km_day = np.clip(rng.lognormal(np.log(120), 0.45, nv), 20, 400)
    dc_share = np.clip(rng.beta(1.2, 5.0, nv), 0, 1)
    amb_bias = (rng.random(nv) - 0.5) * 3
    mean_soc = 30 + rng.random(nv) * 55

    rows = nv * snapshots
    vi = np.repeat(np.arange(nv), snapshots)
    day = rng.random(rows) * 120  # days since the vehicle joined the simulation

    def trace(centre: np.ndarray, n: int = 80) -> tuple[np.ndarray, np.ndarray]:
        moving = rng.random((len(centre), n)) < rng.uniform(0.1, 0.7, (len(centre), 1))
        v = np.clip(rng.normal(centre[:, None], 18.0, (len(centre), n)), 2.0, MAX_SPEED_KMH)
        return np.where(moving, v, 0.0), moving

    recent, moving = trace(duty[vi])
    future, _ = trace(np.clip(duty[vi] + rng.normal(0, 4, rows), 15, 80))
    nominal_kwh_km = cap[vi] / rng_km[vi]
    f_recent = distance_weighted_factor(recent)
    f_future = distance_weighted_factor(future)
    y_kpsp = rng_km[vi] / 100.0 / f_future  # km per 1 % SoC on the next trips

    mv = np.where(moving, recent, np.nan)
    with warnings.catch_warnings():  # rows with no moving sample -> NaN, exactly like a parked live vehicle
        warnings.simplefilter("ignore", RuntimeWarning)
        mean_speed = np.nanmean(mv, axis=1)
        p90 = np.nanpercentile(mv, 90, axis=1)
    moving_frac = moving.mean(1)
    obs_eff = nominal_kwh_km * f_recent * (1 + rng.normal(0, 0.03, rows))
    drove = rng.random(rows) > 0.3
    obs_eff = np.where(drove & (moving_frac > 0), obs_eff, np.nan)

    rate = np.where(fast[vi], SOH_FAST_PER_KWH, SOH_NORMAL_PER_KWH)
    kwh_day = km_day[vi] * nominal_kwh_km * f_recent
    soh_now = np.clip(soh0[vi] - rate * kwh_day * day, 0, 100)
    future_kwh = kwh_day * HORIZON_DAYS * np.clip(rng.normal(1.0, 0.12, rows), 0.5, 1.5)
    y_soh30 = np.clip(soh_now - rate * future_kwh, 0, 100)
    charged = rng.random(rows) < 0.5
    slope = np.where(charged, rate * 100 * (1 + rng.normal(0, 0.2, rows)), np.nan)
    odo = odo0[vi] + km_day[vi] * day
    ambient = 26 + amb_bias[vi] + rng.normal(0, 2.5, rows)

    hist = rng.dirichlet(np.ones(SOC_BINS) * 0.6, rows)
    centre_bin = np.clip((mean_soc[vi] // 10).astype(int), 0, SOC_BINS - 1)
    hist[np.arange(rows), centre_bin] += 1.0
    hist /= hist.sum(1, keepdims=True)

    vins = np.array([f"SYN{seed:03d}{i:011d}"[:17] for i in range(nv)])
    cols: dict[str, np.ndarray] = {
        "vin": vins[vi],
        "vehicle_id": np.full(rows, -1, dtype=np.int64),
        "tenant_id": np.zeros(rows, dtype=np.int64),
        "model_name": np.array([CATALOGUE[m][0] for m in model])[vi],
        "battery_kwh": cap[vi],
        "nominal_range_km": rng_km[vi],
        "soc_pct": rng.uniform(5, 100, rows),
        "soh_pct": soh_now + rng.normal(0, 0.02, rows),
        "mean_speed_kmh": mean_speed,
        "p90_speed_kmh": p90,
        "moving_frac": moving_frac,
        "ambient_c_mean": ambient,
        "ambient_c_max": ambient + rng.uniform(1, 6, rows),
        "hot_frac": np.clip((ambient - 30) / 10, 0, 1) * rng.random(rows),
        "obs_kwh_per_km": obs_eff,
        "km_per_day": km_day[vi] * rng.normal(1.0, 0.1, rows),
        "kwh_charged_per_100km": nominal_kwh_km * f_recent * 100,
        "dc_kwh_share": dc_share[vi],
        "charging_frac": np.clip(rng.normal(0.12, 0.05, rows), 0, 1),
        "soh_slope_per_100kwh": slope,
        "odo_km": odo,
        "cycles": odo / rng_km[vi],
        "soc_std": rng.uniform(5, 25, rows),
        "soc_min": np.clip(mean_soc[vi] - rng.uniform(10, 40, rows), 0, 100),
        "fast_degrade": fast[vi],
        "y_km_per_soc_pct": y_kpsp,
        "y_soh_30d": y_soh30,
    }
    for i in range(SOC_BINS):
        cols[f"soc_hist_{i}"] = hist[:, i]
    return Frame(cols)


# --- live source --------------------------------------------------------------------------------------------


class Conn(Protocol):
    """The subset of psycopg.Connection (dict_row factory) used here; tests pass a stub."""

    def execute(self, query: Any, params: Any = ...) -> Any: ...


_AGG_SQL = """
WITH r AS (
  SELECT vin, ts, speed_kmh, soc_pct, soh_pct, odo_km, ambient_c, charging_state, charging_kw,
         lag(ts) OVER w AS pts, lag(soc_pct) OVER w AS psoc, lag(odo_km) OVER w AS podo,
         lag(charging_state) OVER w AS pcs
  FROM telemetry
  WHERE ts >= %(since)s AND ts < %(until)s AND soc_pct IS NOT NULL
  WINDOW w AS (PARTITION BY vin ORDER BY ts, seq)
), d AS (
  SELECT *, LEAST(GREATEST(EXTRACT(EPOCH FROM ts - pts), 0), 120) AS dt,
         (charging_state = 0 AND pcs = 0 AND odo_km > podo AND psoc >= soc_pct AND psoc > 0.5) AS driving
  FROM r
), a AS (
  SELECT vin, count(*) AS n,
         avg(speed_kmh) FILTER (WHERE speed_kmh > 1) AS mean_speed_kmh,
         percentile_cont(0.9) WITHIN GROUP (ORDER BY speed_kmh) FILTER (WHERE speed_kmh > 1) AS p90_speed_kmh,
         avg((speed_kmh > 1)::int) AS moving_frac,
         avg(ambient_c) AS ambient_c_mean, max(ambient_c) AS ambient_c_max,
         avg((ambient_c > 35)::int) AS hot_frac,
         max(odo_km) - min(odo_km) AS km_window, max(odo_km) AS odo_km,
         sum(psoc - soc_pct) FILTER (WHERE driving) AS soc_drop_driving,
         sum(odo_km - podo) FILTER (WHERE driving) AS km_driving,
         coalesce(sum(charging_kw * dt) FILTER (WHERE charging_state > 0), 0) / 3600.0 AS kwh_charged,
         coalesce(sum(charging_kw * dt) FILTER (WHERE charging_state = 2), 0) / 3600.0 AS kwh_dc,
         avg((charging_state > 0)::int) AS charging_frac,
         last(soc_pct, ts) AS soc_pct, last(soh_pct, ts) AS soh_pct, first(soh_pct, ts) AS soh_first,
         stddev_pop(soc_pct) AS soc_std, min(soc_pct) AS soc_min,
         EXTRACT(EPOCH FROM max(ts) - min(ts)) / 3600.0 AS hours
  FROM d GROUP BY vin
)
SELECT v.id AS vehicle_id, v.tenant_id, a.*, m.name AS model_name,
       m.battery_capacity_kwh::float8 AS battery_kwh, m.nominal_range_km::float8 AS nominal_range_km
FROM a JOIN vehicle v ON v.vin = a.vin JOIN vehicle_model m ON m.id = v.model_id
WHERE m.battery_capacity_kwh > 0 AND m.nominal_range_km > 0
"""

_HIST_SQL = """
SELECT vin, LEAST(GREATEST(floor(avg_soc / 10)::int, 0), 9) AS bin, count(*) AS n
FROM telemetry_5m
WHERE bucket >= %(since)s AND bucket < %(until)s AND avg_soc IS NOT NULL
GROUP BY 1, 2
"""

MIN_KM_FOR_EFFICIENCY = 2.0
MIN_SOC_DROP_FOR_EFFICIENCY = 0.5
MIN_KWH_FOR_SOH_SLOPE = 5.0


def _f(x: Any) -> float:
    return float("nan") if x is None else float(x)


def features_from_rows(rows: Iterable[Mapping[str, Any]], hist_rows: Iterable[Mapping[str, Any]]) -> Frame:
    """Turn the per-vehicle SQL aggregates (+ SoC-histogram rows) into the same columns the synthetic frame
    has. Pure function: unit-tested without a database."""
    rows = [r for r in rows if r.get("soc_pct") is not None]
    hist: dict[str, np.ndarray] = {}
    for h in hist_rows:
        arr = hist.setdefault(str(h["vin"]).strip(), np.zeros(SOC_BINS))
        arr[int(h["bin"])] += float(h["n"])
    out: dict[str, list[Any]] = {k: [] for k in ("vin", "vehicle_id", "tenant_id", "model_name")}
    num_cols = (
        "battery_kwh", "nominal_range_km", "soc_pct", "soh_pct", "mean_speed_kmh", "p90_speed_kmh",
        "moving_frac", "ambient_c_mean", "ambient_c_max", "hot_frac", "obs_kwh_per_km", "km_per_day",
        "kwh_charged_per_100km", "dc_kwh_share", "charging_frac", "soh_slope_per_100kwh", "odo_km", "cycles",
        "soc_std", "soc_min", "km_window", "kwh_charged",
    )  # fmt: skip
    for c in (*num_cols, *SOC_HIST):
        out[c] = []
    for r in rows:
        vin = str(r["vin"]).strip()
        cap, rng_km = _f(r["battery_kwh"]), _f(r["nominal_range_km"])
        km_drv, drop = _f(r.get("km_driving")), _f(r.get("soc_drop_driving"))
        eff = (
            drop / 100.0 * cap / km_drv
            if km_drv >= MIN_KM_FOR_EFFICIENCY and drop >= MIN_SOC_DROP_FOR_EFFICIENCY
            else float("nan")
        )
        kwh = _f(r.get("kwh_charged")) if r.get("kwh_charged") is not None else 0.0
        kwh_dc = _f(r.get("kwh_dc")) if r.get("kwh_dc") is not None else 0.0
        soh, soh_first = _f(r.get("soh_pct")), _f(r.get("soh_first"))
        slope = (soh_first - soh) / kwh * 100.0 if kwh >= MIN_KWH_FOR_SOH_SLOPE else float("nan")
        hours = max(_f(r.get("hours")), 0.25) if r.get("hours") is not None else float("nan")
        km_window = _f(r.get("km_window"))
        odo = _f(r.get("odo_km"))
        counts = hist.get(vin, np.zeros(SOC_BINS))
        shares = counts / counts.sum() if counts.sum() > 0 else np.full(SOC_BINS, np.nan)
        out["vin"].append(vin)
        out["vehicle_id"].append(int(r["vehicle_id"]))
        out["tenant_id"].append(int(r["tenant_id"]))
        out["model_name"].append(str(r.get("model_name") or ""))
        vals = {
            "battery_kwh": cap,
            "nominal_range_km": rng_km,
            "soc_pct": _f(r["soc_pct"]),
            "soh_pct": soh,
            "mean_speed_kmh": _f(r.get("mean_speed_kmh")),
            "p90_speed_kmh": _f(r.get("p90_speed_kmh")),
            "moving_frac": _f(r.get("moving_frac")),
            "ambient_c_mean": _f(r.get("ambient_c_mean")),
            "ambient_c_max": _f(r.get("ambient_c_max")),
            "hot_frac": _f(r.get("hot_frac")),
            "obs_kwh_per_km": eff,
            "km_per_day": km_window / hours * 24.0 if hours == hours else float("nan"),
            "kwh_charged_per_100km": kwh / km_window * 100.0 if km_window >= 1 else float("nan"),
            "dc_kwh_share": kwh_dc / kwh if kwh > 0 else float("nan"),
            "charging_frac": _f(r.get("charging_frac")),
            "soh_slope_per_100kwh": slope,
            "odo_km": odo,
            "cycles": odo / rng_km if rng_km > 0 else float("nan"),
            "soc_std": _f(r.get("soc_std")),
            "soc_min": _f(r.get("soc_min")),
            "km_window": km_window,
            "kwh_charged": kwh,
        }
        for k, v in vals.items():
            out[k].append(v)
        for i in range(SOC_BINS):
            out[f"soc_hist_{i}"].append(float(shares[i]))
    cols: dict[str, np.ndarray] = {
        "vin": np.array(out["vin"], dtype=object),
        "vehicle_id": np.array(out["vehicle_id"], dtype=np.int64),
        "tenant_id": np.array(out["tenant_id"], dtype=np.int64),
        "model_name": np.array(out["model_name"], dtype=object),
    }
    for c in (*num_cols, *SOC_HIST):
        cols[c] = np.array(out[c], dtype=float)
    return Frame(cols)


def _fetch(conn: Conn, sql: str, params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(conn.execute(sql, params).fetchall())


def load_live(conn: Conn, *, until: datetime, hours: float = 6.0) -> Frame:
    """Per-vehicle live features for (until - hours, until]. Only battery vehicles with SoC readings."""
    p = {"since": until - timedelta(hours=hours), "until": until}
    return features_from_rows(_fetch(conn, _AGG_SQL, p), _fetch(conn, _HIST_SQL, p))


MIN_LABEL_KM = 5.0
MIN_LABEL_SOC_DROP = 1.0


def load_live_range_labels(conn: Conn, *, until: datetime, hours: float = 6.0) -> Frame:
    """Live supervised rows for the range model: features from the first half of the window, label = realised
    km per SoC % while driving in the second half. Vehicles that did not drive enough in either half are
    dropped. Each vehicle yields at most one row, so the vehicle split still applies unchanged."""
    mid = until - timedelta(hours=hours / 2)
    first = load_live(conn, until=mid, hours=hours / 2)
    p2 = {"since": mid, "until": until}
    second = {str(r["vin"]).strip(): r for r in _fetch(conn, _AGG_SQL, p2)}
    keep, labels = [], []
    for i, vin in enumerate(first["vin"]):
        r = second.get(str(vin))
        if r is None:
            continue
        km, drop = _f(r.get("km_driving")), _f(r.get("soc_drop_driving"))
        if (
            km >= MIN_LABEL_KM
            and drop >= MIN_LABEL_SOC_DROP
            and first["obs_kwh_per_km"][i] == first["obs_kwh_per_km"][i]
        ):
            keep.append(i)
            labels.append(km / drop)
    out = first.take(np.array(keep, dtype=np.int64))
    out.cols["y_km_per_soc_pct"] = np.array(labels, dtype=float)
    return out
