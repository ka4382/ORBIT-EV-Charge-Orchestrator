from __future__ import annotations

from typing import Any

import pytest
import structlog
from orbit_api import main as main_mod
from orbit_api.db import Database, rows_to_dicts
from orbit_api.main import Resources, create_app


class FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetchval(self, sql, *args):
        return 1

    def transaction(self):
        return FakeTx()


class FakePool:
    def __init__(self, conn: FakeConn, fail: bool = False) -> None:
        self.conn = conn
        self.closed = False
        self.fail = fail

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                if pool.fail:
                    raise ConnectionError("down")
                return pool.conn

            async def __aexit__(self, *a):
                return False

        return Ctx()

    async def close(self):
        self.closed = True


async def test_database_tenant_scope_sets_local_guc():
    conn = FakeConn()
    db = Database(FakePool(conn), FakePool(conn))
    async with db.tenant(42) as c:
        assert c is conn
    assert conn.executed[0][0].startswith("SELECT set_config('app.tenant_id'") and conn.executed[0][1] == (
        "42",
    )
    assert await db.ping() is True
    await db.close()
    assert db.app_pool.closed and db.admin_pool.closed
    assert await Database(FakePool(conn, fail=True), FakePool(conn)).ping() is False
    assert rows_to_dicts([{"a": 1}]) == [{"a": 1}]


async def test_lifespan_connect_failures_are_soft(monkeypatch):
    """When Postgres/Redis/Kafka are all down the app still boots and /readyz says so."""

    async def boom(*a, **k):
        raise ConnectionError("down")

    class BadKafka:
        async def start(self):
            raise ConnectionError("no broker")

        async def stop(self):
            pass

    monkeypatch.setattr(main_mod.Database, "connect", boom)
    monkeypatch.setattr(main_mod, "KafkaPlanPublisher", BadKafka)
    monkeypatch.setattr(
        main_mod.Redis, "from_url", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("x"))
    )
    app = create_app(Resources())
    async with app.router.lifespan_context(app):
        assert app.state.db is None and app.state.store is None
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/readyz")
            assert r.status_code == 503 and r.json() == {"kafka": False, "postgres": False, "redis": False}
            r = await c.get("/api/v1/me", headers={"Authorization": "Bearer x"})
            assert r.status_code == 401


async def test_lifespan_loads_chargers_into_redis(monkeypatch, redis):
    """Startup path with a fake DB: chargers are pushed into geo:chargers / charger:{id}."""
    rows = [
        {
            "id": 9,
            "tenant_id": 1,
            "depot_id": 1,
            "name": "D1-C9",
            "lat": 13.0,
            "lon": 80.0,
            "max_kw": 22,
            "connector": "Type2",
            "is_public": False,
            "price_per_kwh": None,
        }
    ]

    class Conn(FakeConn):
        async def fetch(self, sql, *args):
            return rows

    conn = Conn()

    async def connect(*a, **k):
        return Database(FakePool(conn), FakePool(conn))

    class OkKafka:
        async def start(self):
            pass

        async def stop(self):
            pass

        async def ready(self):
            return True

        async def publish_plan_requested(self, e):
            pass

    monkeypatch.setattr(main_mod.Database, "connect", connect)
    monkeypatch.setattr(main_mod, "KafkaPlanPublisher", OkKafka)
    monkeypatch.setattr(main_mod.Redis, "from_url", lambda *a, **k: redis)
    app = create_app(Resources())
    async with app.router.lifespan_context(app):
        assert await redis.hget("charger:9", "name") == "D1-C9"
        assert await redis.zcard("geo:chargers") == 1
        assert app.state.audit is not None and app.state.rate_limiter is not None


async def test_charger_load_failure_is_logged(monkeypatch, redis):
    class Conn(FakeConn):
        async def fetch(self, sql, *args):
            raise RuntimeError("no table")

    res = Resources(db=Database(FakePool(Conn()), FakePool(Conn())), redis=redis)
    await main_mod._load_chargers(res, structlog.get_logger())
    assert await redis.zcard("geo:chargers") == 0
    await main_mod._load_chargers(Resources(), structlog.get_logger())


async def test_kafka_publisher_wrapper(monkeypatch):
    from orbit_api import publisher as pub_mod

    class Producer:
        def __init__(self):
            self.sent = []
            self.client = self

        async def start(self):
            pass

        async def stop(self):
            pass

        async def send_and_wait(self, topic, value, key=None):
            self.sent.append((topic, value, key))

        async def fetch_all_metadata(self):
            return True

    monkeypatch.setattr(pub_mod, "make_producer", lambda: Producer())
    p = pub_mod.KafkaPlanPublisher()
    assert await p.ready() is False
    with pytest.raises(RuntimeError):
        await p.publish_plan_requested({"vehicle_id": 1})
    await p.start()
    assert await p.ready() is True
    await p.publish_plan_requested({"vehicle_id": 5, "plan_id": 1})
    assert p._producer.sent[0][0] == "plans.requested" and p._producer.sent[0][2] == b"5"
    await p.stop()
    assert await p.ready() is False
