"""Row formatting for the `telemetry` hypertable (COPY text format) and the Parquet archive."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pyarrow as pa
from orbit_common.events import CanonicalEvent, ChargingCode

COPY_COLUMNS: tuple[str, ...] = (
    "ts",
    "vin",
    "seq",
    "tenant_id",
    "lat",
    "lon",
    "speed_kmh",
    "soc_pct",
    "soh_pct",
    "odo_km",
    "ambient_c",
    "charging_state",
    "charging_kw",
    "dtc",
    "evt",
)
COPY_SQL = f"COPY telemetry ({', '.join(COPY_COLUMNS)}) FROM STDIN"  # noqa: S608 - constant column list
NULL = "\\N"


def charging_code(state: str) -> int:
    """NONE/AC/DC -> 0/1/2 (SMALLINT column)."""
    return int(ChargingCode[state])


def copy_escape(text: str) -> str:
    """Escape a text value for COPY ... FROM STDIN (text format)."""
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def pg_array_literal(items: Iterable[str]) -> str:
    """Postgres text[] literal with every element double-quoted, e.g. {"P0301","P0A80"}."""
    quoted = ['"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"' for s in items]
    return "{" + ",".join(quoted) + "}"


def _num(v: float | None) -> str:
    return NULL if v is None else repr(float(v))


def copy_row(e: CanonicalEvent) -> str:
    """One tab-separated COPY line (with trailing newline) in COPY_COLUMNS order."""
    return (
        "\t".join(
            (
                e.ts.isoformat(),
                e.vin,
                str(e.seq),
                str(e.tenant_id),
                repr(e.lat),
                repr(e.lon),
                repr(float(e.speed_kmh)),
                _num(e.soc_pct),
                _num(e.soh_pct),
                repr(float(e.odo_km)),
                _num(e.ambient_c),
                str(charging_code(e.charging_state)),
                repr(float(e.charging_kw)),
                copy_escape(pg_array_literal(e.dtc)),
                NULL if e.evt is None else copy_escape(e.evt),
            )
        )
        + "\n"
    )


def row_key(e: CanonicalEvent) -> tuple[str, int, datetime]:
    """Matches the unique index telemetry_vin_seq_uidx (vin, seq, ts)."""
    return (e.vin, e.seq, e.ts)


def should_persist(seq: int, divisor: int) -> bool:
    """Down-sampling gate for Postgres: with divisor 10 only every 10th sequence number is kept."""
    return divisor <= 1 or seq % divisor == 0


# --- Parquet -----------------------------------------------------------------------------------------
PARQUET_SCHEMA = pa.schema(
    [
        pa.field("ts", pa.timestamp("ms", tz="UTC"), nullable=False),
        pa.field("ingest_ts", pa.timestamp("ms", tz="UTC"), nullable=False),
        pa.field("vin", pa.string(), nullable=False),
        pa.field("seq", pa.int64(), nullable=False),
        pa.field("tenant_id", pa.int32(), nullable=False),
        pa.field("oem", pa.string(), nullable=False),
        pa.field("lat", pa.float64(), nullable=False),
        pa.field("lon", pa.float64(), nullable=False),
        pa.field("speed_kmh", pa.float32(), nullable=False),
        pa.field("soc_pct", pa.float32()),
        pa.field("soh_pct", pa.float32()),
        pa.field("odo_km", pa.float64(), nullable=False),
        pa.field("fuel_pct", pa.float32()),
        pa.field("ambient_c", pa.float32()),
        pa.field("dtc", pa.list_(pa.string()), nullable=False),
        pa.field("evt", pa.string()),
        pa.field("charging_state", pa.string(), nullable=False),
        pa.field("charging_kw", pa.float32(), nullable=False),
        pa.field("schema_version", pa.int32(), nullable=False),
    ]
)
PARQUET_COLUMNS: tuple[str, ...] = tuple(f.name for f in PARQUET_SCHEMA)


@dataclass(frozen=True, slots=True)
class Partition:
    dt: str  # YYYY-MM-DD (UTC, from vehicle ts)
    hour: str  # HH
    tenant_id: int

    def object_key(self, part_id: str | None = None) -> str:
        part = part_id or uuid.uuid4().hex
        return f"dt={self.dt}/hour={self.hour}/tenant_id={self.tenant_id}/part-{part}.parquet"


def partition_for(e: CanonicalEvent) -> Partition:
    return Partition(e.ts.strftime("%Y-%m-%d"), e.ts.strftime("%H"), e.tenant_id)


def parquet_record(e: CanonicalEvent) -> tuple:
    return (
        e.ts,
        e.ingest_ts,
        e.vin,
        e.seq,
        e.tenant_id,
        e.oem,
        e.lat,
        e.lon,
        e.speed_kmh,
        e.soc_pct,
        e.soh_pct,
        e.odo_km,
        e.fuel_pct,
        e.ambient_c,
        list(e.dtc),
        e.evt,
        e.charging_state,
        e.charging_kw,
        e.schema_version,
    )


def to_arrow_table(records: Sequence[tuple]) -> pa.Table:
    columns: list[Sequence[Any]] = (
        list(zip(*records, strict=True)) if records else [[] for _ in PARQUET_COLUMNS]
    )
    return pa.table(
        {
            name: pa.array(col, type=f.type)
            for name, col, f in zip(PARQUET_COLUMNS, columns, PARQUET_SCHEMA, strict=True)
        },
        schema=PARQUET_SCHEMA,
    )
