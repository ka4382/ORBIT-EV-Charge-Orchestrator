from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError
from orbit_archiver.infrastructure.postgres import (
    STAGE_COPY,
    STAGE_CREATE,
    STAGE_MERGE,
    PostgresTelemetryRepo,
)
from orbit_archiver.infrastructure.s3 import S3ObjectStore
from orbit_archiver.settings import ArchiverSettings
from psycopg import errors


class FakeCopy:
    def __init__(self, conn: FakeConn, sql: str) -> None:
        self.conn, self.sql = conn, sql

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        if self.sql.startswith("COPY telemetry (") and self.conn.dup_on_direct:
            raise errors.UniqueViolation("duplicate key")
        return False

    async def write(self, data: str) -> None:
        self.conn.written.append((self.sql, data))


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def copy(self, sql: str) -> FakeCopy:
        return FakeCopy(self.conn, sql)

    async def execute(self, sql: str) -> None:
        self.conn.executed.append(sql)


class FakeConn:
    def __init__(self, dup_on_direct: bool) -> None:
        self.dup_on_direct = dup_on_direct
        self.written: list[tuple[str, str]] = []
        self.executed: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def connection(self) -> Any:
        conn = self.conn

        class Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return Ctx()


async def test_copy_happy_path():
    conn = FakeConn(dup_on_direct=False)
    repo = PostgresTelemetryRepo(FakePool(conn))  # type: ignore[arg-type]
    assert await repo.copy(["a\n", "b\n"]) == 2
    assert conn.written == [
        (
            "COPY telemetry (ts, vin, seq, tenant_id, lat, lon, speed_kmh, soc_pct, soh_pct, odo_km, "
            "ambient_c, charging_state, charging_kw, dtc, evt) FROM STDIN",
            "a\nb\n",
        )
    ]
    assert conn.commits == 1 and conn.rollbacks == 0 and repo.fallbacks == 0
    assert await repo.copy([]) == 0


async def test_copy_falls_back_to_staging_on_unique_violation():
    conn = FakeConn(dup_on_direct=True)
    repo = PostgresTelemetryRepo(FakePool(conn))  # type: ignore[arg-type]
    assert await repo.copy(["a\n"]) == 1
    assert conn.rollbacks == 1 and conn.commits == 1 and repo.fallbacks == 1
    assert conn.executed == [STAGE_CREATE, STAGE_MERGE]
    assert conn.written[-1] == (STAGE_COPY, "a\n")
    assert "ON CONFLICT (vin, seq, ts) DO NOTHING" in STAGE_MERGE
    assert isinstance(PostgresTelemetryRepo.from_url("postgresql://u:p@localhost/x"), PostgresTelemetryRepo)


class FakeS3Client:
    def __init__(self, exists: bool, error_code: str = "404") -> None:
        self.exists = exists
        self.error_code = error_code
        self.created: list[str] = []
        self.puts: list[tuple[str, str, bytes]] = []

    def head_bucket(self, Bucket: str) -> None:  # noqa: N803 - boto3 API
        if not self.exists:
            raise ClientError({"Error": {"Code": self.error_code, "Message": "x"}}, "HeadBucket")

    def create_bucket(self, Bucket: str) -> None:  # noqa: N803
        self.created.append(Bucket)
        self.exists = True

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:  # noqa: N803
        self.puts.append((Bucket, Key, Body))


async def test_s3_store_creates_bucket_and_puts():
    c = FakeS3Client(exists=False)
    store = S3ObjectStore(c, "orbit-telemetry")
    assert await store.ensure_bucket() is True and c.created == ["orbit-telemetry"]
    assert await store.ensure_bucket() is False
    await store.put("dt=x/part-1.parquet", b"data")
    assert c.puts == [("orbit-telemetry", "dt=x/part-1.parquet", b"data")]


async def test_s3_store_propagates_other_errors():
    store = S3ObjectStore(FakeS3Client(exists=False, error_code="403"), "b")
    with pytest.raises(ClientError):
        await store.ensure_bucket()


def test_from_settings_and_env(monkeypatch):
    store = S3ObjectStore.from_settings(
        endpoint="http://localhost:9000",
        access_key="a",
        secret_key="b",  # noqa: S106 - dummy test credential
        bucket="c",
    )
    assert store.bucket == "c"
    monkeypatch.setenv("ARCHIVER_SAMPLE_DIVISOR", "10")
    assert ArchiverSettings().sample_divisor == 10
