"""Service-local tunables (env prefix INGEST_). Shared settings come from orbit_common.config."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INGEST_", env_file=".env", extra="ignore")

    raw_topic: str = "telemetry.raw"
    canonical_topic: str = "telemetry.canonical"
    dlq_topic: str = "telemetry.dlq"
    group: str = "ingest"
    batch_timeout_ms: int = 50
    batch_max_records: int = 2000
    max_future_s: float = 300.0
    dedupe_capacity: int = 2_000_000
    dedupe_window_s: float = 600.0
    max_in_flight_batches: int = 4
    buffer_high_water_bytes: int = 32 * 1024 * 1024
    buffer_low_water_bytes: int = 8 * 1024 * 1024
    dtc_topk: int = 20
    dtc_refresh_s: float = 10.0


@lru_cache
def ingest_settings() -> IngestSettings:
    return IngestSettings()
