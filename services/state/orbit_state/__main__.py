"""Wiring: python -m orbit_state"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import sys

from orbit_common import kafka as kafka_helpers
from orbit_common.config import settings
from orbit_common.logging import configure
from orbit_common.metrics import serve

from orbit_state import SERVICE
from orbit_state.application.runner import consume, summary_ticker
from orbit_state.application.service import StateService
from orbit_state.domain.summary import FleetTracker
from orbit_state.domain.throttle import PublishThrottle
from orbit_state.infrastructure.redis_store import RedisStateStore
from orbit_state.settings import state_settings


async def main() -> int:
    log = configure(SERVICE)
    logging.getLogger("aiokafka").setLevel(logging.WARNING)
    cfg = state_settings()
    serve()
    await kafka_helpers.wait_for_kafka()
    store = RedisStateStore.from_url(
        settings().redis_url,
        pool_size=cfg.redis_pool_size,
        ttl_s=cfg.state_ttl_s,
        summary_ttl_s=cfg.summary_ttl_s,
        max_commands=cfg.pipeline_max_commands,
        replica=socket.gethostname(),
    )
    await store.ping()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    consumer = kafka_helpers.consumer(cfg.topic, group=cfg.group, max_poll_records=cfg.batch_max_records)
    await consumer.start()
    service = StateService(
        store,
        tracker=FleetTracker(cfg.online_window_s),
        throttle=PublishThrottle(cfg.publish_throttle_s),
        topic=cfg.topic,
    )
    ticker = asyncio.create_task(summary_ticker(service, stop, cfg.summary_interval_s, log))
    log.info("state.start", topic=cfg.topic, group=cfg.group)
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
        log.error("state.failed", error=repr(e))
        rc = 1
    finally:
        stop.set()
        await asyncio.gather(ticker, return_exceptions=True)
        try:
            await service.publish_summaries()  # final snapshot so the dashboard is fresh after a rollout
        except Exception as e:  # noqa: BLE001
            log.warning("state.final_summary.failed", error=repr(e))
        await consumer.stop()
        await store.close()
        log.info("state.stopped", rc=rc)
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
