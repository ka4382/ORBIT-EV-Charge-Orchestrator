"""Service-local tunables (env prefix ARCHIVER_, e.g. ARCHIVER_SAMPLE_DIVISOR=10)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ArchiverSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARCHIVER_", env_file=".env", extra="ignore")

    topic: str = "telemetry.canonical"
    group: str = "archiver"
    batch_timeout_ms: int = 50
    batch_max_records: int = 2000
    pg_flush_rows: int = 5000
    pg_flush_interval_s: float = 1.0
    parquet_interval_s: float = 600.0
    parquet_max_rows: int = 500_000  # extra flush trigger so a 100K/s stream never holds 10 min in RAM
    sample_divisor: int = 1  # 10 => only seq % 10 == 0 goes to Postgres
    # 1 => the Parquet lake keeps every event; raise only to protect a laptop disk
    parquet_sample_divisor: int = 1
    pg_pool_min: int = 1
    pg_pool_max: int = 2
    s3_region: str = "us-east-1"


@lru_cache
def archiver_settings() -> ArchiverSettings:
    return ArchiverSettings()
