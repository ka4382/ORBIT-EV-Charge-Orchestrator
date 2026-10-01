"""Wiring: python -m orbit_alert"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from orbit_common import kafka as kafka_helpers
from orbit_common.config import settings
from orbit_common.logging import configure
from orbit_common.metrics import serve

from orbit_alert import SERVICE
from orbit_alert.application.engine import AlertEngine, VehicleCache
from orbit_alert.application.runner import consume
from orbit_alert.infrastructure.kafka import KafkaAlertPublisher
from orbit_alert.infrastructure.postgres import PostgresAlertRepo
from orbit_alert.infrastructure.redis_store import RedisAlertStore
from orbit_alert.settings import alert_settings


async def main() -> int:
    log = configure(SERVICE)
    logging.getLogger("aiokafka").setLevel(logging.WARNING)
    cfg = alert_settings()
    shared = settings()
    serve()
    await kafka_helpers.wait_for_kafka()

    redis_store = RedisAlertStore.from_url(
        shared.redis_url,
        pool_size=cfg.redis_pool_size,
        dedupe_ttl_s=cfg.dedupe_ttl_s,
        soc_ttl_s=cfg.soc_window_s,
    )
    await redis_store.ping()
    repo = PostgresAlertRepo.from_url(shared.database_url, min_size=cfg.pg_pool_min, max_size=cfg.pg_pool_max)
    await repo.open()
    if not await redis_store.chargers_loaded():
        n = await redis_store.load_chargers(await repo.chargers())
        log.info("alert.chargers.loaded", count=n)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    raw_producer = kafka_helpers.producer()
    consumer = kafka_helpers.consumer(cfg.topic, group=cfg.group, max_poll_records=cfg.batch_max_records)
    await raw_producer.start()
    await consumer.start()
    engine = AlertEngine(
        VehicleCache(repo, chunk=cfg.lookup_chunk, negative_ttl_s=cfg.negative_cache_ttl_s),
        redis_store,
        redis_store,
        repo,
        KafkaAlertPublisher(raw_producer, cfg.alerts_topic),
        redis_store,
        topic=cfg.topic,
        default_nominal_range_km=cfg.default_nominal_range_km,
        soc_bucket_s=cfg.soc_bucket_s,
        soc_window_s=cfg.soc_window_s,
        logger=log,
    )
    log.info("alert.start", topic=cfg.topic, group=cfg.group)
    rc = 0
    try:
        await consume(
            consumer,
            engine,
            stop,
            batch_timeout_ms=cfg.batch_timeout_ms,
            batch_max_records=cfg.batch_max_records,
            logger=log,
        )
    except Exception as e:  # noqa: BLE001
        log.error("alert.failed", error=repr(e))
        rc = 1
    finally:
        stop.set()
        await raw_producer.stop()
        await consumer.stop()
        await repo.close()
        await redis_store.close()
        log.info("alert.stopped", rc=rc)
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
