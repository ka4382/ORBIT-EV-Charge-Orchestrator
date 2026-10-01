"""Stub psycopg connection for orbit_ml tests: routes SQL by content, records writes."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _Cursor:
    def __init__(self, conn: StubConn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        self.conn.inserted.extend(rows)
        self.conn.statements.append(sql)


def agg_row(vin: str, vehicle_id: int, tenant_id: int = 1, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "vin": vin,
        "vehicle_id": vehicle_id,
        "tenant_id": tenant_id,
        "model_name": "A-Van 60",
        "battery_kwh": 60.0,
        "nominal_range_km": 320.0,
        "n": 100,
        "mean_speed_kmh": 45.0,
        "p90_speed_kmh": 70.0,
        "moving_frac": 0.4,
        "ambient_c_mean": 28.0,
        "ambient_c_max": 31.0,
        "hot_frac": 0.0,
        "km_window": 40.0,
        "odo_km": 32_000.0,
        "soc_drop_driving": 12.0,
        "km_driving": 36.0,
        "kwh_charged": 20.0,
        "kwh_dc": 5.0,
        "charging_frac": 0.1,
        "soc_pct": 55.0,
        "soh_pct": 94.0,
        "soh_first": 94.008,
        "soc_std": 8.0,
        "soc_min": 40.0,
        "hours": 6.0,
    }
    row.update(over)
    return row


class StubConn:
    def __init__(
        self,
        agg: list[dict[str, Any]] | None = None,
        hist: list[dict[str, Any]] | None = None,
        agg_second: list[dict[str, Any]] | None = None,
        columns: tuple[str, ...] = ("tenant_id", "soh_30d_pct"),
    ) -> None:
        self.agg = agg or []
        self.agg_second = agg_second if agg_second is not None else self.agg
        self.hist = hist or []
        self.columns = columns
        self.statements: list[str] = []
        self.params: list[Any] = []
        self.inserted: list[tuple[Any, ...]] = []
        self.agg_calls = 0
        self.transactions = 0

    def __enter__(self) -> StubConn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> _Result:
        self.statements.append(sql)
        self.params.append(params)
        if "FROM telemetry_5m" in sql:
            return _Result(self.hist)
        if "FROM telemetry" in sql:
            self.agg_calls += 1
            # load_live_range_labels: 1st call = first half (features), 2nd call = second half (labels)
            return _Result(self.agg if self.agg_calls % 2 == 1 else self.agg_second)
        if "information_schema.columns" in sql:
            return _Result([{"column_name": c} for c in self.columns])
        return _Result([])

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):  # type: ignore[no-untyped-def]
        self.transactions += 1
        yield
