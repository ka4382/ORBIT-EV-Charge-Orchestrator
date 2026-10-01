"""Testcontainers fixtures for the stream-processing integration suite (Redpanda, TimescaleDB, Redis).

Run with: cd services && uv run pytest -m integration tests/integration
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Iterator

import psycopg
import pytest
from testcontainers.kafka import RedpandaContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "db" / "migrations"
REDPANDA_IMAGE = os.environ.get("IT_REDPANDA_IMAGE", "redpandadata/redpanda:v24.2.7")
TIMESCALE_IMAGE = os.environ.get("IT_TIMESCALE_IMAGE", "timescale/timescaledb-ha:pg16")
REDIS_IMAGE = os.environ.get("IT_REDIS_IMAGE", "redis:7.4-alpine")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def kafka_bootstrap() -> Iterator[str]:
    with RedpandaContainer(image=REDPANDA_IMAGE) as rp:
        yield rp.get_bootstrap_server()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    pg = PostgresContainer(
        TIMESCALE_IMAGE,
        username="postgres",
        password="postgres",  # noqa: S106 - throwaway container credential
        dbname="orbit",
        driver=None,
    )
    pg.with_command("postgres -c shared_preload_libraries=timescaledb -c max_connections=100")
    with pg:
        url = pg.get_connection_url()
        with psycopg.connect(url, autocommit=True) as conn:
            for f in sorted(MIGRATIONS.glob("*.sql")):
                conn.execute(f.read_text())
        yield url


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer(REDIS_IMAGE) as r:
        yield f"redis://{r.get_container_host_ip()}:{r.get_exposed_port(6379)}/0"


@pytest.fixture(scope="session")
def orbit_env(kafka_bootstrap: str, database_url: str, redis_url: str) -> dict[str, str]:
    """Point orbit_common.config.settings() at the containers (cache is cleared so the env is re-read)."""
    from orbit_common.config import settings

    os.environ.update(
        {"KAFKA_BOOTSTRAP": kafka_bootstrap, "DATABASE_URL": database_url, "REDIS_URL": redis_url}
    )
    settings.cache_clear()
    return {"kafka": kafka_bootstrap, "postgres": database_url, "redis": redis_url}
