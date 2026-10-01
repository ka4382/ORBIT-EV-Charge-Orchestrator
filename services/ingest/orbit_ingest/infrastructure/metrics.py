"""Ingest-only metrics (orbit_common.metrics holds the shared ones; we must not edit it)."""

from __future__ import annotations

import asyncio
import contextlib

from prometheus_client import Gauge

from orbit_ingest.domain.processing import DtcTracker

DTC_TOPK = Gauge("orbit_dtc_topk", "top-K DTC codes seen on the raw stream (Count-Min estimate)", ["code"])
BUFFERED_BYTES = Gauge("orbit_ingest_producer_buffered_bytes", "bytes sent to the producer but not yet acked")
PAUSED = Gauge("orbit_ingest_paused", "1 while partitions are paused for back-pressure")


def refresh_dtc_topk(tracker: DtcTracker) -> None:
    DTC_TOPK.clear()
    for code, n in tracker.top():
        DTC_TOPK.labels(code=code).set(n)


async def dtc_topk_refresher(tracker: DtcTracker, stop: asyncio.Event, interval_s: float = 10.0) -> None:
    while not stop.is_set():
        refresh_dtc_topk(tracker)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
    refresh_dtc_topk(tracker)
