"""Wiring: python -m orbit_archiver"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from orbit_common import kafka as kafka_helpers
from orbit_common.config import settings
from orbit_common.logging import configure
from orbit_common.metrics import serve

from orbit_archiver import SERVICE
from orbit_archiver.application.archiver import ArchiverService
from orbit_archiver.application.runner import consume
from orbit_archiver.infrastructure.postgres import PostgresTelemetryRepo
from orbit_archiver.infrastructure.s3 import S3ObjectStore
from orbit_archiver.settings import archiver_settings


async def main() -> int:
    log = configure(SERVICE)
    logging.getLogger("aiokafka").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    cfg = archiver_settings()
    shared = settings()
    serve()
    await kafka_helpers.wait_for_kafka()

    repo = PostgresTelemetryRepo.from_url(
        shared.database_url, min_size=cfg.pg_pool_min, max_size=cfg.pg_pool_max
    )
    await repo.open()
    store = S3ObjectStore.from_settings(
        endpoint=shared.s3_endpoint,
        access_key=shared.s3_access_key,
        secret_key=shared.s3_secret_key,
        bucket=shared.s3_bucket,
        region=cfg.s3_region,
    )
    if await store.ensure_bucket():
        log.info("archiver.bucket.created", bucket=shared.s3_bucket)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    consumer = kafka_helpers.consumer(cfg.topic, group=cfg.group, max_poll_records=cfg.batch_max_records)
    await consumer.start()
    service = ArchiverService(
        repo,
        store,
        topic=cfg.topic,
        pg_flush_rows=cfg.pg_flush_rows,
        pg_flush_interval_s=cfg.pg_flush_interval_s,
        parquet_interval_s=cfg.parquet_interval_s,
        parquet_max_rows=cfg.parquet_max_rows,
        sample_divisor=cfg.sample_divisor,
        parquet_sample_divisor=cfg.parquet_sample_divisor,
        logger=log,
    )
    log.info("archiver.start", topic=cfg.topic, group=cfg.group, sample_divisor=cfg.sample_divisor)
    rc = 0
    try:
        await consume(
            consumer,
            service,
            stop,
            batch_timeout_ms=cfg.batch_timeout_ms,
            batch_max_records=cfg.batch_max_records,
            logger=log,
        )
    except Exception as e:  # noqa: BLE001
        log.error("archiver.failed", error=repr(e))
        rc = 1
    finally:
        stop.set()
        await consumer.stop()
        await repo.close()
        log.info("archiver.stopped", rc=rc, fallbacks=repo.fallbacks)
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
