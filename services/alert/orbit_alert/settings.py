"""Service-local tunables (env prefix ALERT_). Shared settings come from orbit_common.config."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AlertSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALERT_", env_file=".env", extra="ignore")

    topic: str = "telemetry.canonical"
    group: str = "alert"
    alerts_topic: str = "alerts"
    batch_timeout_ms: int = 50
    batch_max_records: int = 2000
    dedupe_ttl_s: int = 900
    default_nominal_range_km: float = 300.0
    soc_bucket_s: int = 300
    soc_window_s: int = 7200
    charge_gap_s: float = 60.0
    lookup_chunk: int = 5000
    negative_cache_ttl_s: float = 300.0
    pg_pool_min: int = 1
    pg_pool_max: int = 4
    redis_pool_size: int = 16


@lru_cache
def alert_settings() -> AlertSettings:
    return AlertSettings()
