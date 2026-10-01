"""ArchiverService: buffers canonical events, COPYs them into Postgres every 1 s / 5000 rows and
rolls Parquet files per (day, hour, tenant) every 10 minutes (or when the buffer grows too large) and on
shutdown."""

from __future__ import annotations

import io
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pyarrow.parquet as pq
import structlog
from orbit_common.events import CanonicalEvent
from orbit_common.metrics import BATCH_LATENCY, E2E_LATENCY, EVENTS_DROPPED, EVENTS_IN, EVENTS_OUT

from orbit_archiver import SERVICE
from orbit_archiver.application.ports import ObjectStore, TelemetryRepository
from orbit_archiver.domain.rows import (
    Partition,
    copy_row,
    parquet_record,
    partition_for,
    row_key,
    should_persist,
    to_arrow_table,
)

_E2E = E2E_LATENCY.labels(SERVICE)  # bound once: labels() per event costs ~3 us at 100K/s

log: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(slots=True)
class FlushStats:
    pg_rows: int = 0
    pg_flushes: int = 0
    pg_dupes_in_batch: int = 0
    parquet_rows: int = 0
    parquet_files: int = 0
    sampled_out: int = 0
    partitions: list[str] = field(default_factory=list)


class ArchiverService:
    def __init__(
        self,
        repo: TelemetryRepository,
        store: ObjectStore,
        *,
        topic: str = "telemetry.canonical",
        pg_flush_rows: int = 5000,
        pg_flush_interval_s: float = 1.0,
        parquet_interval_s: float = 600.0,
        parquet_max_rows: int = 500_000,
        sample_divisor: int = 1,
        parquet_sample_divisor: int = 1,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] | None = None,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._repo = repo
        self._store = store
        self._topic = topic
        self._pg_rows = pg_flush_rows
        self._pg_interval = pg_flush_interval_s
        self._pq_interval = parquet_interval_s
        self._pq_max_rows = parquet_max_rows
        self._divisor = max(1, sample_divisor)
        self._pq_divisor = max(1, parquet_sample_divisor)
        self._mono = monotonic
        self._clock = clock or (lambda: datetime.now(UTC))
        self._log = logger or log
        self._pg_buf: dict[tuple, str] = {}
        self._pq_buf: dict[Partition, list[tuple]] = {}
        self._pq_rows = 0
        now = self._mono()
        self._pg_last = now
        self._pq_last = now
        self.stats = FlushStats()

    # -- ingest ---------------------------------------------------------------------------------------
    def add(self, events: Sequence[CanonicalEvent]) -> None:
        EVENTS_IN.labels(SERVICE, self._topic).inc(len(events))
        now = self._clock()
        for e in events:
            _E2E.observe(max(0.0, (now - e.ts).total_seconds()))
            if should_persist(e.seq, self._divisor):
                key = row_key(e)
                if key in self._pg_buf:
                    self.stats.pg_dupes_in_batch += 1
                self._pg_buf[key] = copy_row(e)
            else:
                self.stats.sampled_out += 1
            if should_persist(e.seq, self._pq_divisor):  # 1 in production: the lake keeps every event
                self._pq_buf.setdefault(partition_for(e), []).append(parquet_record(e))
                self._pq_rows += 1

    @property
    def pending_pg_rows(self) -> int:
        return len(self._pg_buf)

    @property
    def pending_parquet_rows(self) -> int:
        return self._pq_rows

    # -- flush policy ---------------------------------------------------------------------------------
    def pg_due(self) -> bool:
        return bool(self._pg_buf) and (
            len(self._pg_buf) >= self._pg_rows or self._mono() - self._pg_last >= self._pg_interval
        )

    def parquet_due(self) -> bool:
        return self._pq_rows > 0 and (
            self._pq_rows >= self._pq_max_rows or self._mono() - self._pq_last >= self._pq_interval
        )

    async def flush_pg(self) -> int:
        """COPY everything buffered for Postgres. Rows stay buffered if the repository raises."""
        self._pg_last = self._mono()
        if not self._pg_buf:
            return 0
        rows = list(self._pg_buf.values())
        started = time.perf_counter()
        n = await self._repo.copy(rows)
        self._pg_buf.clear()
        BATCH_LATENCY.labels(SERVICE).observe(time.perf_counter() - started)
        EVENTS_OUT.labels(SERVICE, "postgres").inc(n)
        self.stats.pg_rows += n
        self.stats.pg_flushes += 1
        return n

    async def flush_parquet(self) -> int:
        """Write one zstd Parquet object per buffered partition. Best effort: a failed partition is dropped
        and counted (Kafka offsets were already committed after the Postgres flush; see README)."""
        self._pq_last = self._mono()
        if not self._pq_buf:
            return 0
        buf, self._pq_buf, self._pq_rows = self._pq_buf, {}, 0
        written = 0
        for part, records in buf.items():
            key = part.object_key()
            try:
                table = to_arrow_table(records)
                sink = io.BytesIO()
                pq.write_table(table, sink, compression="zstd")
                await self._store.put(key, sink.getvalue())
            except Exception as e:  # noqa: BLE001
                EVENTS_DROPPED.labels(SERVICE, "parquet_failed").inc(len(records))
                self._log.error("archiver.parquet.failed", key=key, rows=len(records), error=repr(e))
                continue
            written += len(records)
            self.stats.parquet_files += 1
            self.stats.partitions.append(key)
        EVENTS_OUT.labels(SERVICE, "parquet").inc(written)
        self.stats.parquet_rows += written
        return written

    async def tick(self) -> tuple[bool, bool]:
        """Run whichever flushes are due. Returns (pg_flushed, parquet_flushed)."""
        pg = pq_ = False
        if self.pg_due():
            await self.flush_pg()
            pg = True
        if self.parquet_due():
            await self.flush_parquet()
            pq_ = True
        return pg, pq_

    async def close(self) -> None:
        await self.flush_pg()
        await self.flush_parquet()
