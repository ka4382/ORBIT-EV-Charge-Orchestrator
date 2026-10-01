"""Consume loop: bounded in-flight batches, ordered offset commits, producer back-pressure."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import asdict
from typing import Any

import structlog
from orbit_common.kafka import safe_commit

from orbit_ingest.application.pipeline import BatchStats, IngestPipeline
from orbit_ingest.application.ports import BufferGauge, ConsumerPort, ProducerPort

log: structlog.stdlib.BoundLogger = structlog.get_logger()


class IngestRunner:
    """Pulls batches with getmany, processes up to `max_in_flight` batches concurrently and commits
    offsets strictly in batch order once a batch's sends are acknowledged.

    Back-pressure: when the producer's un-acked bytes exceed `high_water` all assigned partitions are
    paused; they are resumed once the buffer drains below `low_water`."""

    def __init__(
        self,
        consumer: ConsumerPort,
        producer: ProducerPort,
        pipeline: IngestPipeline,
        buffer: BufferGauge,
        *,
        batch_timeout_ms: int = 50,
        batch_max_records: int = 2000,
        max_in_flight: int = 4,
        high_water: int = 32 * 1024 * 1024,
        low_water: int = 8 * 1024 * 1024,
        logger: Any = None,
    ) -> None:
        self._consumer = consumer
        self._producer = producer
        self._pipeline = pipeline
        self._buffer = buffer
        self._timeout_ms = batch_timeout_ms
        self._max_records = batch_max_records
        self._sem = asyncio.Semaphore(max_in_flight)
        self._high, self._low = high_water, low_water
        self._inflight: deque[tuple[asyncio.Task[BatchStats], dict[Any, int]]] = deque()
        self._to_commit: dict[Any, int] = {}
        self._paused = False
        self._log = logger or log
        self.batches = 0
        self.totals = BatchStats()

    @property
    def paused(self) -> bool:
        return self._paused

    # -- back-pressure ----------------------------------------------------------------------------
    def _apply_backpressure(self) -> None:
        pending = self._buffer.bytes
        if self._paused:
            # a rebalance resets pause state; keep newly assigned partitions paused too
            assigned = set(self._consumer.assignment())
            not_paused = assigned - set(self._consumer.paused())
            if not_paused and pending > self._low:
                self._consumer.pause(*not_paused)
        if not self._paused and pending > self._high:
            parts = self._consumer.assignment()
            if parts:
                self._consumer.pause(*parts)
                self._paused = True
                self._log.warning("backpressure.pause", buffered_bytes=pending)
        elif self._paused and pending < self._low:
            parts = set(self._consumer.paused()) & set(self._consumer.assignment())
            if parts:
                self._consumer.resume(*parts)
            self._paused = False
            self._log.info("backpressure.resume", buffered_bytes=pending)

    # -- commit bookkeeping --------------------------------------------------------------------------
    def _merge_offsets(self, offsets: dict[Any, int]) -> None:
        for tp, off in offsets.items():
            if off > self._to_commit.get(tp, -1):
                self._to_commit[tp] = off

    async def _reap(self, *, block: bool) -> None:
        """Pop completed batches from the head of the queue (in order) and commit their offsets."""
        while self._inflight:
            task, offsets = self._inflight[0]
            if not task.done():
                if not block:
                    break
                await asyncio.wait({task})
            self._inflight.popleft()
            stats = task.result()  # re-raises if the batch failed -> service exits, offsets stay uncommitted
            self._accumulate(stats)
            self._merge_offsets(offsets)
        if self._to_commit:
            await safe_commit(self._consumer, self._to_commit)
            self._to_commit = {}

    def _accumulate(self, stats: BatchStats) -> None:
        self.batches += 1
        t = self.totals
        t.consumed += stats.consumed
        t.produced += stats.produced
        t.duplicates += stats.duplicates
        t.dlq += stats.dlq
        for k, v in stats.reasons.items():
            t.reasons[k] = t.reasons.get(k, 0) + v

    # -- main loop ----------------------------------------------------------------------------------
    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                await self._reap(block=False)
                self._apply_backpressure()
                await self._sem.acquire()
                try:
                    batches = await self._consumer.getmany(
                        timeout_ms=self._timeout_ms, max_records=self._max_records
                    )
                except BaseException:
                    self._sem.release()
                    raise
                if not batches:
                    self._sem.release()
                    continue
                records = [r for recs in batches.values() for r in recs]
                offsets = {tp: recs[-1].offset + 1 for tp, recs in batches.items() if recs}
                task = asyncio.create_task(self._pipeline.process_batch(records))
                task.add_done_callback(lambda _t: self._sem.release())
                self._inflight.append((task, offsets))
        finally:
            await self._drain()

    async def _drain(self) -> None:
        self._log.info("runner.drain", inflight=len(self._inflight))
        try:
            await self._reap(block=True)
        finally:
            await self._producer.flush()
            if self._to_commit:  # flush raised mid-way; still try to persist what completed
                await safe_commit(self._consumer, self._to_commit)
                self._to_commit = {}
        self._log.info("runner.stopped", batches=self.batches, **asdict(self.totals))


def quiet_kafka_logs() -> None:
    logging.getLogger("aiokafka").setLevel(logging.WARNING)
