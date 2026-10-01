"""Wiring: python -m orbit_ingest"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys

from orbit_common import kafka as kafka_helpers
from orbit_common.logging import configure
from orbit_common.metrics import serve
from orbit_common.streaming import RollingBloom

from orbit_ingest import SERVICE
from orbit_ingest.application.pipeline import IngestPipeline
from orbit_ingest.application.runner import IngestRunner, quiet_kafka_logs
from orbit_ingest.domain.processing import DtcTracker
from orbit_ingest.infrastructure.kafka import BufferTracker, KafkaProducer
from orbit_ingest.infrastructure.metrics import BUFFERED_BYTES, PAUSED, dtc_topk_refresher
from orbit_ingest.settings import ingest_settings


async def main() -> int:
    log = configure(SERVICE)
    quiet_kafka_logs()
    cfg = ingest_settings()
    serve()
    await kafka_helpers.wait_for_kafka()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    raw_producer = kafka_helpers.producer()
    consumer = kafka_helpers.consumer(cfg.raw_topic, group=cfg.group, max_poll_records=cfg.batch_max_records)
    await raw_producer.start()
    await consumer.start()
    tracker = BufferTracker()
    producer = KafkaProducer(raw_producer, tracker)
    dtc = DtcTracker(k=cfg.dtc_topk)
    pipeline = IngestPipeline(
        producer,
        RollingBloom(cfg.dedupe_capacity, window_s=cfg.dedupe_window_s),
        dtc,
        raw_topic=cfg.raw_topic,
        canonical_topic=cfg.canonical_topic,
        dlq_topic=cfg.dlq_topic,
        max_future_s=cfg.max_future_s,
    )
    runner = IngestRunner(
        consumer,
        producer,
        pipeline,
        tracker,
        batch_timeout_ms=cfg.batch_timeout_ms,
        batch_max_records=cfg.batch_max_records,
        max_in_flight=cfg.max_in_flight_batches,
        high_water=cfg.buffer_high_water_bytes,
        low_water=cfg.buffer_low_water_bytes,
        logger=log,
    )
    gauges = asyncio.create_task(_gauge_loop(tracker, runner, stop))
    refresher = asyncio.create_task(dtc_topk_refresher(dtc, stop, cfg.dtc_refresh_s))
    log.info("ingest.start", topic=cfg.raw_topic, group=cfg.group)
    rc = 0
    try:
        await runner.run(stop)
    except Exception as e:  # noqa: BLE001 - crash loudly, offsets of the failed batch stay uncommitted
        log.error("ingest.failed", error=repr(e))
        rc = 1
    finally:
        stop.set()
        await asyncio.gather(gauges, refresher, return_exceptions=True)
        await raw_producer.stop()
        await consumer.stop()
        log.info("ingest.stopped", rc=rc)
    return rc


async def _gauge_loop(tracker: BufferTracker, runner: IngestRunner, stop: asyncio.Event) -> None:
    while not stop.is_set():
        BUFFERED_BYTES.set(tracker.bytes)
        PAUSED.set(1 if runner.paused else 0)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1.0)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
