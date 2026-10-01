"""32-dim degradation-profile embedding per vehicle and its pgvector persistence.

Vector = 32 interpretable features (no learned projection, so every dimension can be explained):
10 SoC-histogram bins, 5 charge-pattern, 3 temperature-exposure, 4 duty-cycle, 4 SoH state/trend,
2 efficiency, 2 battery size, 2 model outputs (predicted 30-day SoH loss, SoH deficit vs the fleet line).
Each is standardised over the live population of this run (NaN -> population mean, clipped to +-5 sd),
multiplied by a group weight (degradation features count double), and the vector is L2-normalised, so
L2 distance in pgvector ranks vehicles by similarity of degradation profile.

Only the newest `battery_health` row of a vehicle carries an embedding: a publish clears the embedding on the
vehicle's older rows, so the HNSW index holds exactly one vector per vehicle and nearest-neighbour results are
distinct vehicles, all from the same model run (same standardisation).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from .data import SOC_HIST, Frame

DIM = 32
EMBED_FEATURES: tuple[str, ...] = (
    *SOC_HIST,
    "soc_std",
    "soc_min",
    "charging_frac",
    "dc_kwh_share",
    "kwh_charged_per_100km",
    "ambient_c_mean",
    "ambient_c_max",
    "hot_frac",
    "mean_speed_kmh",
    "p90_speed_kmh",
    "moving_frac",
    "km_per_day",
    "soh_pct",
    "soh_slope_per_100kwh",
    "cycles",
    "odo_km",
    "obs_kwh_per_km",
    "efficiency_ratio",
    "battery_kwh",
    "nominal_range_km",
    "pred_soh_drop_30d",
    "soh_deficit",
)
assert len(EMBED_FEATURES) == DIM
WEIGHTS = np.array(
    [0.5] * len(SOC_HIST)
    + [0.8] * 5
    + [0.8] * 3
    + [0.8] * 4
    + [2.0, 2.0, 1.0, 1.0]  # SoH level, SoH slope, cycles, odometer
    + [1.0, 1.0]
    + [0.7, 0.7]
    + [2.0, 2.0]  # predicted loss, deficit
)
CLIP_SD = 5.0


@dataclass
class Standardiser:
    mean: np.ndarray
    std: np.ndarray

    @staticmethod
    def fit(x: np.ndarray) -> Standardiser:
        finite = np.isfinite(x)
        n = finite.sum(0)
        safe = np.where(finite, x, 0.0)
        mean = np.where(n > 0, safe.sum(0) / np.maximum(n, 1), 0.0)
        var = np.where(n > 0, (np.where(finite, x - mean, 0.0) ** 2).sum(0) / np.maximum(n, 1), 0.0)
        std = np.sqrt(var)
        return Standardiser(mean=mean, std=np.where(std > 1e-12, std, 1.0))

    def transform(self, x: np.ndarray) -> np.ndarray:
        z = (x - self.mean) / self.std
        return np.clip(np.where(np.isfinite(z), z, 0.0), -CLIP_SD, CLIP_SD)


def add_model_columns(frame: Frame, soh_30d: np.ndarray, loss_per_km: float) -> Frame:
    """Derived columns used by the embedding (efficiency ratio, predicted 30-day loss, deficit vs fleet)."""
    cols = dict(frame.cols)
    nominal_eff = np.asarray(frame["battery_kwh"], float) / np.asarray(frame["nominal_range_km"], float)
    cols["efficiency_ratio"] = np.asarray(frame["obs_kwh_per_km"], float) / nominal_eff
    cols["pred_soh_drop_30d"] = np.asarray(frame["soh_pct"], float) - np.asarray(soh_30d, float)
    # fleet line: SoH 100 at 0 km, losing `loss_per_km` per km of odometer
    expected = 100.0 - loss_per_km * np.asarray(frame["odo_km"], float)
    cols["soh_deficit"] = expected - np.asarray(frame["soh_pct"], float)
    return Frame(cols)


def embed(frame: Frame, scaler: Standardiser | None = None) -> tuple[np.ndarray, Standardiser]:
    """(n, 32) float32 matrix of unit vectors, plus the standardiser used (fitted here if not given)."""
    x = frame.matrix(EMBED_FEATURES)
    scaler = scaler or Standardiser.fit(x)
    z = scaler.transform(x) * WEIGHTS
    norm = np.linalg.norm(z, axis=1, keepdims=True)
    uniform = np.full((1, DIM), 1.0 / np.sqrt(DIM))
    out = np.where(norm > 1e-12, z / np.where(norm > 1e-12, norm, 1.0), uniform)
    return out.astype(np.float32), scaler


def vector_literal(v: Sequence[float]) -> str:
    """pgvector text input format: [x1,x2,...]."""
    return "[" + ",".join(f"{float(x):.6f}" for x in v) + "]"


class Conn(Protocol):
    def execute(self, query: Any, params: Any = ...) -> Any: ...
    def cursor(self) -> Any: ...


REQUIRED_COLUMNS = ("tenant_id", "soh_30d_pct")  # added by db/migrations/0004_ml.sql
_SCHEMA_SQL = (
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name = 'battery_health' AND column_name = ANY(%(cols)s)"
)


def require_schema(conn: Conn) -> None:
    found = {r["column_name"] for r in conn.execute(_SCHEMA_SQL, {"cols": list(REQUIRED_COLUMNS)}).fetchall()}
    missing = [c for c in REQUIRED_COLUMNS if c not in found]
    if missing:
        raise RuntimeError(f"battery_health lacks {missing}: apply db/migrations/0004_ml.sql (db/migrate.py)")


_CLEAR_SQL = (
    "UPDATE battery_health SET embedding = NULL "
    "WHERE vehicle_id = ANY(%(ids)s) AND embedding IS NOT NULL AND computed_ts < %(ts)s"
)
_INSERT_SQL = (
    "INSERT INTO battery_health (vehicle_id, tenant_id, computed_ts, soh_pct, cycles, method, embedding, "
    "soh_30d_pct) VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s)"
)


def battery_health_rows(
    frame: Frame, vectors: np.ndarray, soh_30d: np.ndarray, computed_ts: Any, method: str
) -> list[tuple[Any, ...]]:
    """Rows for vehicles with a SoH reading; NUMERIC(5,2) columns are clamped to [0, 100]."""
    rows = []
    for i in range(len(frame)):
        soh = float(frame["soh_pct"][i])
        if not np.isfinite(soh):
            continue
        cycles = float(frame["cycles"][i])
        f30 = float(soh_30d[i])
        rows.append(
            (
                int(frame["vehicle_id"][i]),
                int(frame["tenant_id"][i]),
                computed_ts,
                round(min(max(soh, 0.0), 100.0), 2),
                int(round(cycles)) if np.isfinite(cycles) else None,
                method,
                vector_literal(vectors[i]),
                round(min(max(f30, 0.0), 100.0), 2) if np.isfinite(f30) else None,
            )
        )
    return rows


def write_battery_health(conn: Conn, rows: list[tuple[Any, ...]], computed_ts: Any, batch: int = 5000) -> int:
    """Clear older embeddings of these vehicles and insert the new rows, in the caller's transaction."""
    if not rows:
        return 0
    ids = sorted({r[0] for r in rows})
    conn.execute(_CLEAR_SQL, {"ids": ids, "ts": computed_ts})
    with conn.cursor() as cur:
        for start in range(0, len(rows), batch):
            cur.executemany(_INSERT_SQL, rows[start : start + batch])
    conn.execute("ANALYZE battery_health")
    return len(rows)
