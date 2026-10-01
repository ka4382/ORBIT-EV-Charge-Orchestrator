"""Publish model outputs where the online path can use them without loading a model:

* Redis `range:{vin}` = JSON {"km_per_soc_pct", "method": "hgb_v1", "mae_km", "trained_at"}, TTL 1 day.
  The API multiplies live SoC by km_per_soc_pct; when the key is missing it falls back to baseline_v0.
* Postgres `battery_health` rows (method 'model_v1') with the 32-dim embedding and the 30-day SoH forecast.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import numpy as np

from .data import Frame
from .embeddings import add_model_columns, battery_health_rows, embed, require_schema, write_battery_health
from .models import RANGE_METHOD, SOH_METHOD, TrainedModels

RANGE_KEY = "range:{vin}"
RANGE_TTL_S = 86_400


class RedisLike(Protocol):
    def pipeline(self, transaction: bool = ...) -> Any: ...


def range_payload(km_per_soc_pct: float, mae_km: float, trained_at: str) -> str:
    return json.dumps(
        {
            "km_per_soc_pct": round(float(km_per_soc_pct), 4),
            "method": RANGE_METHOD,
            "mae_km": round(float(mae_km), 2),
            "trained_at": trained_at,
        },
        separators=(",", ":"),
    )


def publish_range(
    redis: RedisLike,
    vins: Any,
    km_per_soc_pct: np.ndarray,
    *,
    mae_km: float,
    trained_at: str,
    batch: int = 2000,
) -> int:
    """SET range:{vin} <json> EX 86400 for every finite, positive coefficient. Returns keys written."""
    written = 0
    pairs = [
        (str(v), float(k)) for v, k in zip(vins, km_per_soc_pct, strict=True) if math.isfinite(k) and k > 0
    ]
    for start in range(0, len(pairs), batch):
        pipe = redis.pipeline(transaction=False)
        for vin, k in pairs[start : start + batch]:
            pipe.set(RANGE_KEY.format(vin=vin), range_payload(k, mae_km, trained_at), ex=RANGE_TTL_S)
        pipe.execute()
        written += len(pairs[start : start + batch])
    return written


@dataclass
class PublishResult:
    vehicles: int
    battery_health_rows: int
    range_keys: int
    computed_ts: str


def publish(models: TrainedModels, live: Frame, conn: Any, redis: RedisLike) -> PublishResult:
    """Predict for every live vehicle, write battery_health (one transaction) and Redis coefficients."""
    computed_ts = datetime.now(UTC)
    if not len(live):
        return PublishResult(0, 0, 0, computed_ts.isoformat())
    require_schema(conn)
    kpsp = models.predict_km_per_soc_pct(live)
    soh30 = models.predict_soh_30d(live)
    enriched = add_model_columns(live, soh30, models.soh_baseline.loss_per_km)
    vectors, _ = embed(enriched)
    rows = battery_health_rows(enriched, vectors, soh30, computed_ts, SOH_METHOD)
    with conn.transaction():
        n_rows = write_battery_health(conn, rows, computed_ts)
    n_keys = publish_range(redis, live["vin"], kpsp, mae_km=models.range_mae_km, trained_at=models.trained_at)
    return PublishResult(len(live), n_rows, n_keys, computed_ts.isoformat())
