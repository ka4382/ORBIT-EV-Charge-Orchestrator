# ruff: noqa: ASYNC110 - tests poll fakes with short sleeps on purpose
import asyncio
from itertools import count

import orjson
from orbit_state.application.runner import consume, decode_records, summary_ticker
from orbit_state.application.service import StateService
from orbit_state.domain.state import LastSeen
from orbit_state.domain.throttle import PublishThrottle
from state_fakes import NOW, VIN, VIN2, FakeStore, ev


def make_service(store: FakeStore, mono=None) -> StateService:
    ticks = count(0, 0.5)
    return StateService(
        store, throttle=PublishThrottle(2.0), clock=lambda: NOW, monotonic=mono or (lambda: next(ticks))
    )


async def test_batch_coalesces_per_vin_and_skips_stale():
    store = FakeStore(existing={VIN: LastSeen(seq=5, ts=NOW)})
    svc = make_service(store)
    stats = await svc.process_batch(
        [ev(seq=4, dt_s=-1), ev(seq=6, dt_s=1), ev(seq=7, dt_s=2), ev(seq=1, vin=VIN2)]
    )
    assert stats.consumed == 4 and stats.stale == 1 and stats.written == 2
    assert store.fetches == [{VIN, VIN2}]
    by_vin = {s.vin: s for s in store.states}
    assert orjson.loads(by_vin[VIN].value)["seq"] == 7  # last write wins within the batch
    assert by_vin[VIN].publish is not None and orjson.loads(by_vin[VIN].publish)["data"]["seq"] == 7
    assert by_vin[VIN2].publish is not None
    # second batch: memory cache is warm, so no fetch; throttle blocks publish within 2 s
    stats2 = await svc.process_batch([ev(seq=8, dt_s=3)])
    assert len(store.fetches) == 1 and stats2.published == 0 and store.states[-1].publish is None


async def test_throttle_allows_after_interval():
    clock = [0.0]
    store = FakeStore()
    svc = make_service(store, mono=lambda: clock[0])
    await svc.process_batch([ev(seq=1)])
    clock[0] = 1.0
    await svc.process_batch([ev(seq=2, dt_s=1)])
    clock[0] = 2.5
    await svc.process_batch([ev(seq=3, dt_s=2)])
    assert [s.publish is not None for s in store.states] == [True, False, True]


async def test_publish_summaries_writes_hash_and_message():
    store = FakeStore()
    svc = make_service(store)
    assert await svc.publish_summaries() == 0
    await svc.process_batch([ev(seq=1, soc_pct=50.0), ev(seq=1, vin=VIN2, soc_pct=30.0, tenant_id=13)])
    assert await svc.publish_summaries() == 2
    sums = {s.tenant_id: s for s in store.summaries}
    assert sums[12].fields["vehicles"] == "1" and sums[13].fields["avg_soc"] == "30.0"
    assert orjson.loads(sums[12].publish)["type"] == "summary"


def test_decode_records_skips_garbage():
    class R:
        def __init__(self, v):
            self.value = v

    out = decode_records([R(ev(seq=1).to_json()), R(b"nope")])
    assert len(out) == 1 and out[0].seq == 1


async def test_consume_loop_commits_and_ticker_runs():
    from aiokafka.structs import TopicPartition

    tp = TopicPartition("telemetry.canonical", 3)

    class R:
        def __init__(self, v, off):
            self.value, self.offset = v, off

    class Consumer:
        def __init__(self):
            self.batches = [{tp: [R(ev(seq=1).to_json(), 7), R(ev(seq=2, dt_s=1).to_json(), 8)]}, {}]
            self.commits = []
            self.owned = {tp for b in self.batches for tp in b}

        async def getmany(self, *, timeout_ms, max_records):
            await asyncio.sleep(0)
            return self.batches.pop(0) if self.batches else {}

        def assignment(self):

            return self.owned

        async def commit(self, offsets):
            self.commits.append(offsets)

    store = FakeStore()
    svc = make_service(store)
    consumer = Consumer()
    stop = asyncio.Event()

    async def stopper():
        while consumer.batches:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.03)
        stop.set()

    await asyncio.gather(consume(consumer, svc, stop), summary_ticker(svc, stop, interval_s=0.01), stopper())
    assert consumer.commits == [{tp: 9}]
    assert store.summaries and store.summaries[-1].fields["vehicles"] == "1"


async def test_summary_ticker_survives_store_errors():
    class Boom(FakeStore):
        async def write(self, batch):
            raise RuntimeError("redis down")

    svc = make_service(Boom())
    svc.tracker.observe(ev(seq=1))
    stop = asyncio.Event()
    task = asyncio.create_task(summary_ticker(svc, stop, interval_s=0.01))
    await asyncio.sleep(0.03)
    stop.set()
    await task  # no exception escaped
