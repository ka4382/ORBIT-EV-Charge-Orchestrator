"""Consume loop shared shape: getmany -> decode -> process -> commit; summary ticker alongside."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict
from typing import Any

import structlog
from orbit_common.events import CanonicalEvent
from orbit_common.kafka import safe_commit
from orbit_common.metrics import EVENTS_DROPPED

from orbit_state import SERVICE
from orbit_state.application.ports import ConsumerPort
from orbit_state.application.service import StateService

log: structlog.stdlib.BoundLogger = structlog.get_logger()


def decode_records(records: list[Any]) -> list[CanonicalEvent]:
    out: list[CanonicalEvent] = []
    bad = 0
    for r in records:
        try:
            out.append(CanonicalEvent.from_json(r.value))
        except Exception:  # noqa: BLE001 - canonical topic should be clean; count and skip
            bad += 1
    if bad:
        EVENTS_DROPPED.labels(SERVICE, "undecodable").inc(bad)
    return out


async def consume(
    consumer: ConsumerPort,
    service: StateService,
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
        stats = await service.process_batch(decode_records(records))
        await safe_commit(consumer, {tp: recs[-1].offset + 1 for tp, recs in polled.items() if recs})
        batches += 1
        if batches % 200 == 0:
            lg.info("state.progress", batches=batches, **asdict(stats))
    lg.info("state.consume.stopped", batches=batches)


async def summary_ticker(
    service: StateService, stop: asyncio.Event, interval_s: float = 5.0, logger: Any = None
) -> None:
    lg = logger or log
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        try:
            await service.publish_summaries()
        except Exception as e:  # noqa: BLE001 - summary is best effort; never kill the consumer
            lg.warning("state.summary.failed", error=repr(e))
