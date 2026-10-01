"""Canonical telemetry event (mirrors contracts/telemetry.canonical.v1.avsc) and JSON codec."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum
from typing import Literal

import orjson
from pydantic import BaseModel, Field, field_validator

from .vin import is_valid as vin_is_valid

ChargingState = Literal["NONE", "AC", "DC"]


class ChargingCode(IntEnum):
    NONE = 0
    AC = 1
    DC = 2


class CanonicalEvent(BaseModel):
    vin: str
    ts: datetime
    ingest_ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    seq: int
    tenant_id: int
    oem: str
    lat: float
    lon: float
    speed_kmh: float
    soc_pct: float | None = None
    soh_pct: float | None = None
    odo_km: float
    fuel_pct: float | None = None
    ambient_c: float | None = None
    dtc: list[str] = Field(default_factory=list)
    evt: str | None = None
    charging_state: ChargingState = "NONE"
    charging_kw: float = 0.0
    schema_version: int = 1

    @field_validator("vin")
    @classmethod
    def _vin(cls, v: str) -> str:
        v = v.upper()
        if not vin_is_valid(v):
            raise ValueError("invalid VIN")
        return v

    @field_validator("lat")
    @classmethod
    def _lat(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("lat out of range")
        return v

    @field_validator("lon")
    @classmethod
    def _lon(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("lon out of range")
        return v

    @field_validator("soc_pct", "soh_pct", "fuel_pct")
    @classmethod
    def _pct(cls, v: float | None) -> float | None:
        if v is not None and not 0 <= v <= 100:
            raise ValueError("percentage out of range")
        return v

    @field_validator("speed_kmh")
    @classmethod
    def _speed(cls, v: float) -> float:
        if not 0 <= v <= 400:
            raise ValueError("speed out of range")
        return v

    @property
    def key(self) -> bytes:
        return self.vin.encode()

    @property
    def idempotency_key(self) -> bytes:
        return f"{self.vin}:{self.seq}".encode()

    def to_json(self) -> bytes:
        return orjson.dumps(self.model_dump(mode="json"))

    @classmethod
    def from_json(cls, raw: bytes) -> CanonicalEvent:
        return cls.model_validate(orjson.loads(raw))
