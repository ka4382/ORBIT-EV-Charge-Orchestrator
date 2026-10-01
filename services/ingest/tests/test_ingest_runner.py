# ruff: noqa: ASYNC110 - tests poll fakes with short sleeps on purpose
import asyncio

import pytest
from aiokafka.structs import TopicPartition
from ingest_fakes import FakeBuffer, FakeConsumer, FakeProducer, SetDeduper, oem_a, rec
from orbit_ingest.application.pipeline import IngestPipeline
from orbit_ingest.application.runner import IngestRunner
from orbit_ingest.infrastructure.kafka import BufferTracker, KafkaProducer

TP = TopicPartition("telemetry.raw", 0)


async def run_until_idle(runner: IngestRunner, consumer: FakeConsumer, stop: asyncio.Event) -> None:
    async def stopper() -> None:
        while consumer._batches or runner._inflight:  # noqa: SLF001
            await asyncio.sleep(0.005)
        stop.set()

    await asyncio.gather(runner.run(stop), stopper())


async def test_runner_commits_after_batches_in_order():
    producer = FakeProducer()
    consumer = FakeConsumer(
        [
            {TP: [rec(oem_a(seq=1), 10), rec(oem_a(seq=2), 11)]},
            {TP: [rec(oem_a(seq=3), 12)]},
            {},
        ]
    )
    runner = IngestRunner(
        consumer, producer, IngestPipeline(producer, SetDeduper()), FakeBuffer(), max_in_flight=2
    )
    await run_until_idle(runner, consumer, asyncio.Event())
    assert consumer.commits and consumer.commits[-1][TP] == 13
    assert all(c[TP] <= n for c, n in zip(consumer.commits, [12, 13, 13, 13], strict=False))
    assert runner.totals.produced == 3 and producer.flushed == 1


async def test_runner_backpressure_pauses_and_resumes():
    producer = FakeProducer()
    consumer = FakeConsumer([{TP: [rec(oem_a(seq=1), 0)]}])
    buffer = FakeBuffer(bytes=100)
    runner = IngestRunner(
        consumer, producer, IngestPipeline(producer, SetDeduper()), buffer, high_water=50, low_water=10
    )
    stop = asyncio.Event()

    async def script() -> None:
        while consumer.pause_calls == 0:
            await asyncio.sleep(0.005)
        assert runner.paused and consumer.paused_parts == {TP}
        buffer.bytes = 0
        while consumer.resume_calls == 0:
            await asyncio.sleep(0.005)
        assert not runner.paused and not consumer.paused_parts
        while consumer._batches:  # noqa: SLF001
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)
        stop.set()

    await asyncio.gather(runner.run(stop), script())
    assert consumer.commits[-1][TP] == 1


async def test_runner_failure_leaves_offsets_uncommitted():
    producer = FakeProducer(fail_topics={"telemetry.canonical"})
    consumer = FakeConsumer([{TP: [rec(oem_a(seq=1), 0)]}])
    runner = IngestRunner(consumer, producer, IngestPipeline(producer, SetDeduper()), FakeBuffer())
    stop = asyncio.Event()

    async def stopper() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    with pytest.raises(RuntimeError):
        await asyncio.gather(runner.run(stop), stopper())
    assert consumer.commits == []
    assert producer.flushed == 1


async def test_kafka_producer_adapter_tracks_buffer():
    class Raw:
        async def send(self, topic, value=None, key=None):
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self.fut = fut
            return fut

        async def flush(self):
            self.flushed = True

    raw = Raw()
    kp = KafkaProducer(raw, BufferTracker())  # type: ignore[arg-type]
    fut = await kp.send("t", b"k", b"value")
    assert kp.tracker.bytes == 6
    raw.fut.set_result(None)
    await fut
    await asyncio.sleep(0)
    assert kp.tracker.bytes == 0
    await kp.flush()
    assert raw.flushed
