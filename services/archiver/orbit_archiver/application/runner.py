"""Consume loop: poll -> buffer -> flush when due -> commit offsets covered by the Postgres flush."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

import structlog
from orbit_common.events import CanonicalEvent
from orbit_common.kafka import safe_commit
from orbit_common.metrics import EVENTS_DROPPED

from orbit_archiver import SERVICE
from orbit_archiver.application.archiver import ArchiverService
from orbit_archiver.application.ports import ConsumerPort

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
    service: ArchiverService,
    stop: asyncio.Event,
    *,
    batch_timeout_ms: int = 50,
    batch_max_records: int = 2000,
    logger: Any = None,
) -> None:
    lg = logger or log
    uncommitted: dict[Any, int] = {}
    flushes = 0
    try:
        while not stop.is_set():
            polled = await consumer.getmany(timeout_ms=batch_timeout_ms, max_records=batch_max_records)
            if polled:
                records = [r for recs in polled.values() for r in recs]
                service.add(decode_records(records))
                for tp, recs in polled.items():
                    if recs:
                        uncommitted[tp] = max(uncommitted.get(tp, -1), recs[-1].offset + 1)
            pg_flushed, _ = await service.tick()
            if pg_flushed or (uncommitted and service.pending_pg_rows == 0):
                if uncommitted:
                    await safe_commit(consumer, uncommitted)
                    uncommitted = {}
                flushes += 1
                if flushes % 60 == 0:
                    lg.info("archiver.progress", **asdict(service.stats))
    finally:
        await service.close()
        if uncommitted:
            await safe_commit(consumer, uncommitted)
        lg.info("archiver.stopped", **asdict(service.stats))
