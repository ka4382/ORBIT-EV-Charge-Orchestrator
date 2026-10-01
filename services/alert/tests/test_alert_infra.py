from __future__ import annotations

from typing import Any

import orjson
from alert_fakes import NOW, VIN
from orbit_alert.application.ports import GeoQuery, SocSample
from orbit_alert.domain.alerts import LOW_SOC, WARN, Alert
from orbit_alert.infrastructure.kafka import KafkaAlertPublisher
from orbit_alert.infrastructure.postgres import PostgresAlertRepo
from orbit_alert.infrastructure.redis_store import (
    GEO_CHARGERS,
    RedisAlertStore,
    charger_key,
    events_channel,
    soc_key,
)
from orbit_alert.settings import AlertSettings


class FakePipeline:
    def __init__(self, parent: FakeRedis) -> None:
        self.parent = parent
        self.cmds: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def _record(*a: Any, **kw: Any) -> None:
            self.cmds.append((name, a, kw))

        return _record

    async def execute(self) -> list[Any]:
        self.parent.executed.append(self.cmds)
        out = []
        for name, a, kw in self.cmds:
            if name == "set":
                out.append(self.parent.keys.add(a[0]) or a[0] not in self.parent.seen_keys)
                self.parent.seen_keys.add(a[0])
            elif name == "geosearch":
                out.append(["c1"] if kw["radius"] >= 10 else [])
            else:
                out.append(1)
        return out


class FakeRedis:
    def __init__(self) -> None:
        self.executed: list[list[Any]] = []
        self.keys: set[str] = set()
        self.seen_keys: set[str] = set()
        self.exists_result = 0

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)

    async def exists(self, key: str) -> int:
        return self.exists_result

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True


async def test_claim_uses_set_nx_ex_and_chunks():
    r = FakeRedis()
    store = RedisAlertStore(r, dedupe_ttl_s=900, max_commands=3)
    won = await store.claim(["a", "b", "a", "c", "d"])
    assert won == [True, True, False, True, True]
    cmd = r.executed[0][0]
    assert cmd[0] == "set" and cmd[2] == {"nx": True, "ex": 900}
    assert len(r.executed) == 2


async def test_reachable_geosearch():
    r = FakeRedis()
    store = RedisAlertStore(r)
    out = await store.reachable([GeoQuery(13.0, 80.0, 30.0), GeoQuery(13.0, 80.0, 1.0)])
    assert out == [True, False]
    name, args, kw = r.executed[0][0]
    assert name == "geosearch" and args == (GEO_CHARGERS,) and kw["unit"] == "km" and kw["longitude"] == 80.0


async def test_publish_alerts_and_soc_mirror():
    r = FakeRedis()
    store = RedisAlertStore(r, soc_ttl_s=7200, soc_entries=24, max_commands=4)
    a = Alert(12, VIN, LOW_SOC, WARN, NOW, {"soc_pct": 1.0})
    await store.publish([a], [SocSample(VIN, 1000, 50.0), SocSample(VIN, 1300, 40.0)])
    flat = [c for b in r.executed for c in b]
    assert flat[0][0] == "publish" and flat[0][1][0] == events_channel(12)
    assert orjson.loads(flat[0][1][1])["type"] == "alert"
    assert [c[0] for c in flat[1:4]] == ["rpush", "ltrim", "expire"]
    assert (
        flat[1][1] == (soc_key(VIN), "1000:50.0")
        and flat[2][1] == (soc_key(VIN), -24, -1)
        and flat[3][1][1] == 7200
    )
    assert all(len(b) <= 4 for b in r.executed)
    await store.publish([], [])
    assert await store.ping()
    await store.close()


async def test_load_chargers_when_missing():
    r = FakeRedis()
    store = RedisAlertStore(r, max_commands=4)
    assert not await store.chargers_loaded()
    rows = [
        {
            "id": 1,
            "name": "D1-C1",
            "lat": 13.0,
            "lon": 80.0,
            "max_kw": 22,
            "price_per_kwh": None,
            "is_public": False,
            "tenant_id": 1,
        },
        {
            "id": 2,
            "name": "Pub",
            "lat": 13.1,
            "lon": 80.1,
            "max_kw": 150,
            "price_per_kwh": 16.0,
            "is_public": True,
            "tenant_id": None,
        },
        {
            "id": 3,
            "name": "Pub2",
            "lat": 13.2,
            "lon": 80.2,
            "max_kw": 50,
            "price_per_kwh": 14.0,
            "is_public": True,
            "tenant_id": None,
        },
    ]
    assert await store.load_chargers(rows) == 3
    flat = [c for b in r.executed for c in b]
    assert flat[0] == ("geoadd", (GEO_CHARGERS, (80.0, 13.0, "1")), {})
    assert (
        flat[1][1][0] == charger_key(1)
        and flat[1][2]["mapping"]["price_per_kwh"] == ""
        and flat[3][2]["mapping"]["is_public"] == "1"
    )
    assert flat[3][2]["mapping"]["tenant_id"] == "" and flat[1][2]["mapping"]["max_kw"] == "22.0"
    assert len(r.executed) >= 2
    r.exists_result = 1
    assert await store.chargers_loaded()


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, rows):
        self.rows, self.executed, self.committed = rows, [], False

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return FakeCursor(self.rows)

    async def commit(self):
        self.committed = True


class FakePool:
    def __init__(self, rows):
        self.conn = FakeConn(rows)

    def connection(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return Ctx()


async def test_postgres_lookup_insert_and_chargers():
    pool = FakePool([(VIN + " ", 7, 300.0)])
    repo = PostgresAlertRepo(pool)  # type: ignore[arg-type]
    out = await repo.lookup([VIN])
    assert out[VIN].vehicle_id == 7 and out[VIN].nominal_range_km == 300.0
    assert await repo.lookup([]) == {}
    pool = FakePool([(11,), (12,)])
    repo = PostgresAlertRepo(pool)  # type: ignore[arg-type]
    a1 = Alert(12, VIN, LOW_SOC, WARN, NOW, {"soc_pct": 1.0}, vehicle_id=7)
    a2 = Alert(12, VIN, "SOH_DROP", WARN, NOW, {}, vehicle_id=7)
    assert await repo.insert([a1, a2]) == [11, 12] and pool.conn.committed
    sql, params = pool.conn.executed[0]
    assert "RETURNING id" in sql and params[1] == [7, 7] and params[5][0] == '{"soc_pct":1.0}'
    assert await repo.insert([]) == []
    pool = FakePool([(1, "n", 13.0, 80.0, 22, None, False, 1)])
    repo = PostgresAlertRepo(pool)  # type: ignore[arg-type]
    rows = await repo.chargers()
    assert rows[0]["name"] == "n" and rows[0]["tenant_id"] == 1
    assert isinstance(PostgresAlertRepo.from_url("postgresql://u:p@localhost/x"), PostgresAlertRepo)


async def test_kafka_publisher_awaits_acks():
    import asyncio

    class Raw:
        def __init__(self):
            self.sent = []

        async def send(self, topic, value=None, key=None):
            self.sent.append((topic, key, value))
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            fut.set_result(None)
            return fut

    raw = Raw()
    pub = KafkaAlertPublisher(raw, "alerts")  # type: ignore[arg-type]
    await pub.emit([Alert(12, VIN, LOW_SOC, WARN, NOW, {})])
    await pub.emit([])
    assert raw.sent[0][0] == "alerts" and raw.sent[0][1] == b"12:" + VIN.encode()


def test_settings(monkeypatch):
    monkeypatch.setenv("ALERT_DEDUPE_TTL_S", "5")
    assert AlertSettings().dedupe_ttl_s == 5
    assert isinstance(RedisAlertStore.from_url("redis://localhost:6379/0"), RedisAlertStore)
