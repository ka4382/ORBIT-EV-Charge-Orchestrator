"""Batch use case: raw records -> canonical/DLQ sends, with dedupe and metrics."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from orbit_common.metrics import BATCH_LATENCY, E2E_LATENCY, EVENTS_DROPPED, EVENTS_IN, EVENTS_OUT

from orbit_ingest import SERVICE
from orbit_ingest.application.ports import ProducerPort, Record
from orbit_ingest.domain.processing import REASON_DUPLICATE, Accepted, Deduper, DtcTracker, decode

_E2E = E2E_LATENCY.labels(SERVICE)  # bound once: labels() per event costs ~3 us at 100K/s


@dataclass(slots=True)
class BatchStats:
    consumed: int = 0
    produced: int = 0
    duplicates: int = 0
    dlq: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


class IngestPipeline:
    def __init__(
        self,
        producer: ProducerPort,
        dedupe: Deduper,
        dtc: DtcTracker | None = None,
        *,
        raw_topic: str = "telemetry.raw",
        canonical_topic: str = "telemetry.canonical",
        dlq_topic: str = "telemetry.dlq",
        max_future_s: float = 300.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._producer = producer
        self._dedupe = dedupe
        self._dtc = dtc or DtcTracker()
        self._raw_topic = raw_topic
        self._canonical = canonical_topic
        self._dlq = dlq_topic
        self._max_future_s = max_future_s
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def dtc(self) -> DtcTracker:
        return self._dtc

    async def process_batch(self, records: Sequence[Record]) -> BatchStats:
        """Decode, dedupe and fan out one batch. Returns after every send in the batch is acknowledged,
        so the caller may commit offsets (at-least-once; downstream is idempotent on (vin, seq))."""
        started = time.perf_counter()
        now = self._clock()
        stats = BatchStats(consumed=len(records))
        EVENTS_IN.labels(SERVICE, self._raw_topic).inc(len(records))
        pending: list = []
        canonical: list[tuple[bytes | None, bytes]] = []
        for rec in records:
            outcome = decode(rec.value or b"", now, self._max_future_s)
            if isinstance(outcome, Accepted):
                event = outcome.event
                if self._dedupe.seen(event.idempotency_key):
                    stats.duplicates += 1
                    stats.reasons[REASON_DUPLICATE] = stats.reasons.get(REASON_DUPLICATE, 0) + 1
                    continue
                self._dtc.record(event.dtc)
                canonical.append((event.key, event.to_json()))
                stats.produced += 1
                _E2E.observe(max(0.0, (now - event.ts).total_seconds()))
            else:
                stats.dlq += 1
                stats.reasons[outcome.reason] = stats.reasons.get(outcome.reason, 0) + 1
                EVENTS_DROPPED.labels(SERVICE, outcome.reason).inc()
                pending.append(await self._producer.send(self._dlq, rec.key, outcome.dlq_payload()))
        if canonical:
            pending.extend(await self._producer.send_many(self._canonical, canonical))
        if pending:
            await asyncio.gather(*pending)
        if stats.duplicates:
            EVENTS_DROPPED.labels(SERVICE, REASON_DUPLICATE).inc(stats.duplicates)
        EVENTS_OUT.labels(SERVICE, self._canonical).inc(stats.produced)
        EVENTS_OUT.labels(SERVICE, self._dlq).inc(stats.dlq)
        BATCH_LATENCY.labels(SERVICE).observe(time.perf_counter() - started)
        return stats
