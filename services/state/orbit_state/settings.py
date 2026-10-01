"""Service-local tunables (env prefix STATE_). Shared settings come from orbit_common.config."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class StateSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STATE_", env_file=".env", extra="ignore")

    topic: str = "telemetry.canonical"
    group: str = "state"
    batch_timeout_ms: int = 50
    batch_max_records: int = 2000
    state_ttl_s: int = 600
    publish_throttle_s: float = 2.0
    summary_interval_s: float = 5.0
    summary_ttl_s: int = 10
    online_window_s: float = 120.0
    pipeline_max_commands: int = 1000
    redis_pool_size: int = 16


@lru_cache
def state_settings() -> StateSettings:
    return StateSettings()
