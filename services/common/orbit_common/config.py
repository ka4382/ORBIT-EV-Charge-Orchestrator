from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """12-factor configuration: everything comes from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    orbit_env: str = "dev"
    kafka_bootstrap: str = "localhost:19092"
    mqtt_url: str = "mqtt://localhost:1883"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/orbit"
    database_app_url: str = "postgresql://orbit_app:orbit_app@localhost:5432/orbit"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "orbit"
    s3_secret_key: str = "orbit-secret"
    s3_bucket: str = "orbit-telemetry"
    jwt_dev_secret: str = "change-me-dev-only"
    jwks_url: str = ""
    jwt_issuer: str = "orbit-dev"
    otel_exporter_otlp_endpoint: str = ""
    metrics_port: int = 9100
    sim_vehicles: int = 100_000
    sim_tenants: int = 8


@lru_cache
def settings() -> Settings:
    return Settings()
