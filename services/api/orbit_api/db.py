"""asyncpg pools. Tenant-scoped work runs on the `orbit_app` pool inside a transaction that sets
`app.tenant_id` locally, so Postgres row-level security (db/migrations/0003_rls.sql) isolates tenants."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import orjson


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=lambda v: orjson.dumps(v).decode(), decoder=orjson.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=lambda v: orjson.dumps(v).decode(), decoder=orjson.loads, schema="pg_catalog"
    )


class Database:
    """Two pools: `app` (RLS-subject role, per-request tenant scope) and `admin` (startup + audit sink)."""

    def __init__(self, app_pool: asyncpg.Pool, admin_pool: asyncpg.Pool) -> None:
        self.app_pool = app_pool
        self.admin_pool = admin_pool

    @classmethod
    async def connect(
        cls, app_url: str, admin_url: str, *, min_size: int = 2, max_size: int = 20
    ) -> Database:
        app_pool = await asyncpg.create_pool(
            app_url, min_size=min_size, max_size=max_size, init=_init_connection, command_timeout=30
        )
        admin_pool = await asyncpg.create_pool(
            admin_url, min_size=1, max_size=max(4, max_size // 4), init=_init_connection, command_timeout=60
        )
        assert app_pool is not None and admin_pool is not None
        return cls(app_pool, admin_pool)

    async def close(self) -> None:
        await self.app_pool.close()
        await self.admin_pool.close()

    @asynccontextmanager
    async def tenant(self, tenant_id: int) -> AsyncIterator[asyncpg.Connection]:
        """`SET LOCAL app.tenant_id` (parameterised as set_config(..., is_local => true)) in a transaction."""
        async with self.app_pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(int(tenant_id)))
            yield conn

    async def ping(self) -> bool:
        try:
            async with self.app_pool.acquire() as conn:
                return await conn.fetchval("SELECT 1") == 1
        except Exception:
            return False


def rows_to_dicts(rows: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
