from __future__ import annotations

from typing import Any

from orbit_state.application.ports import StateWrite, SummaryWrite, WriteBatch
from orbit_state.infrastructure.redis_store import (
    RedisStateStore,
    events_channel,
    geo_key,
    summary_part_key,
    summary_parts_key,
    veh_key,
)
from orbit_state.settings import StateSettings
from state_fakes import NOW, VIN, VIN2


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
        for name, a, _ in self.cmds:
            if name == "hmget":
                out.append(self.parent.hashes.get(a[0], [None, None]))
            else:
                out.append(True)
        return out


class FakeRedis:
    def __init__(self) -> None:
        self.executed: list[list[Any]] = []
        self.hashes: dict[str, list[Any]] = {}
        self.values: dict[str, bytes] = {}
        self.mgets: list[list[str]] = []

    async def mget(self, keys: list[str]) -> list[bytes | None]:
        self.mgets.append(list(keys))
        return [self.values.get(k) for k in keys]
        self.pinged = False
        self.closed = False

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction is False
        return FakePipeline(self)

    async def ping(self) -> bool:
        self.pinged = True
        return True

    async def aclose(self) -> None:
        self.closed = True


async def test_fetch_last_seen_parses_hash():
    r = FakeRedis()
    r.values[veh_key(VIN)] = b'{"seq": 42, "ts": "2026-09-25T10:15:02Z"}'
    store = RedisStateStore(r, max_commands=1)  # forces chunking (min is STATE_COMMANDS)
    out = await store.fetch_last_seen([VIN, VIN2])
    assert out[VIN].seq == 42 and out[VIN].ts == NOW and VIN2 not in out
    assert await store.ping() and r.pinged
    await store.close()
    assert r.closed


async def test_write_pipelines_state_and_summary_with_cap():
    r = FakeRedis()
    store = RedisStateStore(r, ttl_s=600, summary_ttl_s=10, max_commands=12)
    states = [
        StateWrite(VIN, 12, 21.0, 72.0, b'{"seq":1}', b"msg"),
        StateWrite(VIN2, 12, 21.0, 72.0, b'{"seq":1}', None),
        StateWrite(VIN, 13, 21.0, 72.0, b'{"seq":2}', b"msg2"),
    ]
    summaries = [SummaryWrite(12, {"vehicles": "2"}, b"sum")]
    await store.write(WriteBatch(states=states, summaries=summaries))
    flat = [c for batch in r.executed for c in batch]
    names = [c[0] for c in flat]
    # per vehicle: HSET+EXPIRE(+PUBLISH); per tenant: one GEOADD and one SADD for first-seen VINs
    # summary still uses HSET; vehicles use SET ... EX
    assert names.count("set") == 3 and names.count("hset") == 1
    # summaries are partials per replica and are not published (the API pushes the merged summary)
    assert names.count("geoadd") == 2 and names.count("publish") == 2
    vin_sadds = [c for c in flat if c[0] == "sadd" and str(c[1][0]).startswith("tenant:")]
    assert len(vin_sadds) == 1  # VIN is registered once, even if a later event carries another tenant
    assert all(len(b) <= 12 for b in r.executed)
    first_set = [c for c in flat if c[0] == "set"][0]
    assert first_set[1] == (veh_key(VIN), b'{"seq":1}') and first_set[2] == {"ex": 600}
    expire = [c for c in flat if c[0] == "expire"]
    assert ("expire", (summary_part_key(12, "0"), 10), {}) in expire
    assert ("sadd", (summary_parts_key(12), "0"), {}) in flat
    geo = [c for c in flat if c[0] == "geoadd"][0]
    assert geo[1] == (geo_key(12), [72.0, 21.0, VIN, 72.0, 21.0, VIN2])
    sadd = [c for c in flat if c[0] == "sadd"]
    assert sadd[0][1][1:] == (VIN, VIN2)
    pub = [c for c in flat if c[0] == "publish"][0]
    assert pub[1] == (events_channel(12), b"msg")


async def test_write_empty_batch_is_noop():
    r = FakeRedis()
    await RedisStateStore(r).write(WriteBatch())
    assert r.executed == []


def test_settings_prefix(monkeypatch):
    monkeypatch.setenv("STATE_PUBLISH_THROTTLE_S", "3")
    assert StateSettings().publish_throttle_s == 3.0


def test_from_url_builds_client():
    store = RedisStateStore.from_url("redis://localhost:6379/0", pool_size=2)
    assert isinstance(store, RedisStateStore)


async def test_sadd_only_for_first_seen_vins():
    r = FakeRedis()
    store = RedisStateStore(r)
    w = StateWrite(VIN, 12, 21.0, 72.0, b'{"seq":1}', None)
    await store.write(WriteBatch(states=[w]))
    await store.write(WriteBatch(states=[w]))
    flat = [c for batch in r.executed for c in batch]
    vin_sadds = [c for c in flat if c[0] == "sadd" and c[1][0] == "tenant:12:vins"]
    assert len(vin_sadds) == 1 and [c[0] for c in flat].count("geoadd") == 2
