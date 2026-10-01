# ruff: noqa: ASYNC110 - tests poll fakes with short sleeps on purpose
import asyncio
import io

import pyarrow.parquet as pq
import pytest
from aiokafka.structs import TopicPartition
from archiver_fakes import VIN2, FakeRepo, FakeStore, ev
from orbit_archiver.application.archiver import ArchiverService
from orbit_archiver.application.runner import consume, decode_records


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(repo=None, store=None, **kw):
    clock = Clock()
    repo = repo or FakeRepo()
    store = store or FakeStore()
    svc = ArchiverService(
        repo,
        store,
        monotonic=clock,
        pg_flush_rows=5,
        pg_flush_interval_s=1.0,
        parquet_interval_s=600.0,
        parquet_max_rows=100,
        **kw,
    )
    return svc, repo, store, clock


async def test_pg_flush_on_row_threshold_and_interval():
    svc, repo, _store, clock = make()
    svc.add([ev(seq=i) for i in range(4)])
    assert not svc.pg_due() and svc.pending_pg_rows == 4
    clock.t += 1.0
    assert svc.pg_due()
    assert await svc.flush_pg() == 4 and len(repo.batches) == 1 and svc.pending_pg_rows == 0
    svc.add([ev(seq=i) for i in range(10, 15)])
    assert svc.pg_due()  # 5 rows >= threshold
    pg, pq_ = await svc.tick()
    assert pg and not pq_ and len(repo.rows) == 9
    assert await svc.flush_pg() == 0


async def test_duplicates_within_buffer_collapse():
    svc, repo, *_ = make()
    svc.add([ev(seq=1), ev(seq=1), ev(seq=2)])
    assert svc.pending_pg_rows == 2 and svc.stats.pg_dupes_in_batch == 1
    await svc.flush_pg()
    assert len(repo.rows) == 2


async def test_sample_divisor_only_affects_postgres():
    svc, repo, store, _clock = make(sample_divisor=10)
    svc.add([ev(seq=i) for i in range(1, 21)])
    assert svc.pending_pg_rows == 2 and svc.pending_parquet_rows == 20 and svc.stats.sampled_out == 18
    await svc.close()
    assert len(repo.rows) == 2
    table = pq.read_table(io.BytesIO(next(iter(store.objects.values()))))
    assert table.num_rows == 20


async def test_parquet_partitions_and_zstd():
    svc, _repo, store, clock = make()
    svc.add([ev(seq=1), ev(seq=2, dt_s=3600), ev(seq=1, vin=VIN2, tenant_id=13)])
    assert not svc.parquet_due()
    clock.t += 600
    assert svc.parquet_due()
    assert await svc.flush_parquet() == 3
    keys = sorted(store.objects)
    assert [k.split("/part-")[0] for k in keys] == [
        "dt=2026-09-25/hour=10/tenant_id=12",
        "dt=2026-09-25/hour=10/tenant_id=13",
        "dt=2026-09-25/hour=11/tenant_id=12",
    ]
    meta = pq.read_metadata(io.BytesIO(store.objects[keys[0]]))
    assert meta.row_group(0).column(0).compression == "ZSTD"
    assert svc.stats.parquet_files == 3 and svc.stats.parquet_rows == 3 and svc.pending_parquet_rows == 0
    assert await svc.flush_parquet() == 0


async def test_parquet_max_rows_triggers_early_flush():
    svc, _repo, store, _clock = make()
    svc.add([ev(seq=i) for i in range(100)])
    assert svc.parquet_due()
    _pg, pq_ = await svc.tick()
    assert pq_ and len(store.objects) == 1


async def test_parquet_failure_is_counted_not_raised():
    store = FakeStore(fail_keys={"tenant_id=13"})
    svc, _repo, _store, _clock = make(store=store)
    svc.add([ev(seq=1), ev(seq=1, vin=VIN2, tenant_id=13)])
    assert await svc.flush_parquet() == 1
    assert len(store.objects) == 1 and svc.stats.parquet_rows == 1


async def test_pg_failure_keeps_rows_buffered():
    svc, repo, *_ = make(repo=FakeRepo(fail=True))
    svc.add([ev(seq=1)])
    with pytest.raises(RuntimeError):
        await svc.flush_pg()
    assert svc.pending_pg_rows == 1


class R:
    def __init__(self, v, off):
        self.value, self.offset = v, off


class Consumer:
    def __init__(self, batches):
        self.batches = batches
        self.commits = []
        self.owned = {tp for b in self.batches for tp in b}

    async def getmany(self, *, timeout_ms, max_records):
        await asyncio.sleep(0)
        return self.batches.pop(0) if self.batches else {}

    def assignment(self):

        return self.owned

    async def commit(self, offsets):
        self.commits.append(dict(offsets))


TP = TopicPartition("telemetry.canonical", 1)


async def test_consume_commits_only_after_pg_flush_and_flushes_on_stop():
    svc, repo, store, clock = make()
    consumer = Consumer(
        [
            {TP: [R(ev(seq=1).to_json(), 0), R(ev(seq=2).to_json(), 1)]},
            {TP: [R(ev(seq=3).to_json(), 2), R(b"bad", 3)]},
        ]
    )
    stop = asyncio.Event()

    async def stopper():
        while consumer.batches:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)
        assert consumer.commits == []  # nothing flushed yet (3 rows < 5, clock frozen)
        clock.t += 1.0  # interval elapsed -> next tick flushes and commits
        await asyncio.sleep(0.02)
        assert consumer.commits == [{TP: 4}]
        stop.set()

    await asyncio.gather(consume(consumer, svc, stop), stopper())
    assert len(repo.rows) == 3 and len(store.objects) == 1  # parquet written on shutdown
    assert consumer.commits == [{TP: 4}]


async def test_consume_commits_when_everything_sampled_out():
    svc, repo, _store, _clock = make(sample_divisor=10)
    consumer = Consumer([{TP: [R(ev(seq=1).to_json(), 0)]}])
    stop = asyncio.Event()

    async def stopper():
        while not consumer.commits:
            await asyncio.sleep(0.005)
        stop.set()

    await asyncio.wait_for(asyncio.gather(consume(consumer, svc, stop), stopper()), timeout=2)
    assert consumer.commits == [{TP: 1}] and repo.rows == []


def test_decode_records_skips_bad():
    assert len(decode_records([R(ev().to_json(), 0), R(b"{", 1)])) == 1


def test_parquet_sample_divisor_limits_lake_rows():
    svc = ArchiverService(FakeRepo(), FakeStore(), parquet_sample_divisor=4)
    svc.add([ev(seq=i, dt_s=i) for i in range(1, 9)])
    assert svc.pending_parquet_rows == 2  # seq 4 and 8
    assert svc.pending_pg_rows == 8  # Postgres sampling is independent
