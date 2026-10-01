"""Redis implementation of StateStore following contracts/redis.md. Pipelines are capped at N commands."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import redis.asyncio as aioredis

from orbit_state.application.ports import StateWrite, SummaryWrite, WriteBatch
from orbit_state.domain.state import LastSeen, last_seen_from_value


def veh_key(vin: str) -> str:
    return f"veh:{vin}"


def tenant_vins_key(tenant_id: int) -> str:
    return f"tenant:{tenant_id}:vins"


def geo_key(tenant_id: int) -> str:
    return f"geo:vehicles:{tenant_id}"


def events_channel(tenant_id: int) -> str:
    return f"events:{tenant_id}"


def summary_key(tenant_id: int) -> str:
    return f"summary:{tenant_id}"


def summary_part_key(tenant_id: int, replica: str) -> str:
    """One partial summary per state replica: each replica only sees the partitions it owns."""
    return f"summary:{tenant_id}:part:{replica}"


def summary_parts_key(tenant_id: int) -> str:
    return f"summary:{tenant_id}:parts"


class RedisStateStore:
    STATE_COMMANDS = 2  # per vehicle: SET EX, (PUBLISH); GEOADD/SADD are batched per tenant

    def __init__(
        self,
        client: aioredis.Redis,
        *,
        ttl_s: int = 600,
        summary_ttl_s: int = 10,
        max_commands: int = 1000,
        replica: str = "0",
    ) -> None:
        self._r = client
        self._replica = replica
        self._ttl = ttl_s
        self._summary_ttl = summary_ttl_s
        self._max = max(self.STATE_COMMANDS, max_commands)
        self._registered: set[str] = set()  # VINs already in tenant:{t}:vins (set membership is permanent)

    @classmethod
    def from_url(cls, url: str, *, pool_size: int = 16, **kw: Any) -> RedisStateStore:
        client = aioredis.Redis.from_url(url, max_connections=pool_size, decode_responses=False)
        return cls(client, **kw)

    async def ping(self) -> bool:
        return bool(await self._r.ping())

    async def close(self) -> None:
        await self._r.aclose()

    async def fetch_last_seen(self, vins: Iterable[str]) -> dict[str, LastSeen]:
        vins = list(vins)
        out: dict[str, LastSeen] = {}
        for i in range(0, len(vins), self._max):
            chunk = vins[i : i + self._max]
            values = await self._r.mget([veh_key(v) for v in chunk])
            for vin, raw in zip(chunk, values, strict=True):
                seen = last_seen_from_value(raw)
                if seen is not None:
                    out[vin] = seen
        return out

    async def write(self, batch: WriteBatch) -> None:
        """Per vehicle: HSET + EXPIRE (+ PUBLISH when throttling allows). Per tenant and batch: one GEOADD
        with every moved vehicle and one SADD with VINs this process has not registered yet. Batching the
        set commands cuts redis-py packing work roughly in half at 100K events/s."""
        pipe = self._r.pipeline(transaction=False)
        n = 0
        geo: dict[int, list[Any]] = {}
        new_members: dict[int, list[str]] = {}
        for st in batch.states:
            n += self._queue_state(pipe, st)
            geo.setdefault(st.tenant_id, []).extend((st.lon, st.lat, st.vin))
            if st.vin not in self._registered:
                self._registered.add(st.vin)
                new_members.setdefault(st.tenant_id, []).append(st.vin)
            if n + self.STATE_COMMANDS > self._max:
                await pipe.execute()
                pipe, n = self._r.pipeline(transaction=False), 0
        for tenant_id, values in geo.items():
            if n + 1 > self._max:
                await pipe.execute()
                pipe, n = self._r.pipeline(transaction=False), 0
            pipe.geoadd(geo_key(tenant_id), values)
            n += 1
        for tenant_id, vins in new_members.items():
            if n + 1 > self._max:
                await pipe.execute()
                pipe, n = self._r.pipeline(transaction=False), 0
            pipe.sadd(tenant_vins_key(tenant_id), *vins)
            n += 1
        for sm in batch.summaries:
            if n + 4 > self._max:  # check before queueing so a pipeline never exceeds the cap
                await pipe.execute()
                pipe, n = self._r.pipeline(transaction=False), 0
            n += self._queue_summary(pipe, sm)
        if n:
            await pipe.execute()

    def _queue_state(self, pipe: aioredis.client.Pipeline, st: StateWrite) -> int:
        pipe.set(veh_key(st.vin), st.value, ex=self._ttl)
        if st.publish is not None:
            pipe.publish(events_channel(st.tenant_id), st.publish)
            return 2
        return 1

    def _queue_summary(self, pipe: aioredis.client.Pipeline, sm: SummaryWrite) -> int:
        """Write this replica's partial summary; the API merges all live partials for the tenant.

        Partials expire after `summary_ttl_s`, so a replica that dies or loses its partitions drops out of
        the merge within seconds. No pub/sub here: the API pushes the merged summary to WebSockets."""
        key = summary_part_key(sm.tenant_id, self._replica)
        fields: Mapping[Any, Any] = sm.fields
        pipe.hset(key, mapping=fields)
        pipe.expire(key, self._summary_ttl)
        pipe.sadd(summary_parts_key(sm.tenant_id), self._replica)
        pipe.expire(summary_parts_key(sm.tenant_id), self._summary_ttl * 6)
        return 4


def _s(v: bytes | str) -> str:
    return v.decode() if isinstance(v, bytes) else v
