"""Consume loop: getmany -> decode -> engine -> commit."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

import structlog
from orbit_common.events import CanonicalEvent
from orbit_common.kafka import safe_commit
from orbit_common.metrics import EVENTS_DROPPED

from orbit_alert import SERVICE
from orbit_alert.application.engine import AlertEngine
from orbit_alert.application.ports import ConsumerPort

log: structlog.stdlib.BoundLogger = structlog.get_logger()


def decode_records(records: list[Any]) -> list[CanonicalEvent]:
    out: list[CanonicalEvent] = []
    bad = 0
    for r in records:
        try:
            out.append(CanonicalEvent.from_json(r.value))
        except Exception:  # noqa: BLE001
            bad += 1
    if bad:
        EVENTS_DROPPED.labels(SERVICE, "undecodable").inc(bad)
    return out


async def consume(
    consumer: ConsumerPort,
    engine: AlertEngine,
    stop: asyncio.Event,
    *,
    batch_timeout_ms: int = 50,
    batch_max_records: int = 2000,
    logger: Any = None,
) -> None:
    lg = logger or log
    batches = 0
    while not stop.is_set():
        polled = await consumer.getmany(timeout_ms=batch_timeout_ms, max_records=batch_max_records)
        if not polled:
            continue
        records = [r for recs in polled.values() for r in recs]
        stats = await engine.process_batch(decode_records(records))
        await safe_commit(consumer, {tp: recs[-1].offset + 1 for tp, recs in polled.items() if recs})
        batches += 1
        if stats.emitted or batches % 200 == 0:
            lg.info("alert.batch", batches=batches, **asdict(stats))
    lg.info("alert.consume.stopped", batches=batches)
