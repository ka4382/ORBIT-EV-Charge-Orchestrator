"""COPY-based loader for the telemetry hypertable.

Happy path: a single `COPY telemetry (...) FROM STDIN` (text format) per micro-batch -- the fastest way
into Postgres. Duplicates (replay after a crash, at-least-once delivery) trip the unique index
(vin, seq, ts), which aborts the COPY. In that case we roll back and COPY the same rows into a
per-transaction TEMP table, then
`INSERT ... SELECT ... ON CONFLICT (vin, seq, ts) DO NOTHING`. The fallback keeps COPY speed for the load
and pays the conflict check only for the rare batch that actually contains replays."""

from __future__ import annotations

from collections.abc import Sequence

from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from orbit_archiver.domain.rows import COPY_COLUMNS, COPY_SQL

_COLS = ", ".join(COPY_COLUMNS)
STAGE_CREATE = "CREATE TEMP TABLE telemetry_stage (LIKE telemetry INCLUDING DEFAULTS) ON COMMIT DROP"
STAGE_COPY = f"COPY telemetry_stage ({_COLS}) FROM STDIN"  # noqa: S608
STAGE_MERGE = (
    f"INSERT INTO telemetry ({_COLS}) SELECT {_COLS} FROM telemetry_stage "  # noqa: S608 - constant column list
    "ON CONFLICT (vin, seq, ts) DO NOTHING"
)


class PostgresTelemetryRepo:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
        self.fallbacks = 0

    @classmethod
    def from_url(cls, url: str, *, min_size: int = 1, max_size: int = 2) -> PostgresTelemetryRepo:
        return cls(AsyncConnectionPool(url, min_size=min_size, max_size=max_size, open=False))

    async def open(self) -> None:
        await self._pool.open()
        await self._pool.wait()

    async def close(self) -> None:
        await self._pool.close()

    async def copy(self, rows: Sequence[str]) -> int:
        if not rows:
            return 0
        data = "".join(rows)
        async with self._pool.connection() as conn:
            try:
                async with conn.cursor() as cur, cur.copy(COPY_SQL) as cp:
                    await cp.write(data)
                await conn.commit()
                return len(rows)
            except errors.UniqueViolation:
                await conn.rollback()
                self.fallbacks += 1
            async with conn.cursor() as cur:
                await cur.execute(STAGE_CREATE)
                async with cur.copy(STAGE_COPY) as cp:
                    await cp.write(data)
                await cur.execute(STAGE_MERGE)
            await conn.commit()
        return len(rows)
