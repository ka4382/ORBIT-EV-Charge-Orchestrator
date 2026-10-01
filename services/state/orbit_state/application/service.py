"""StateService: apply a batch of canonical events to the store and emit throttled pub/sub messages."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from orbit_common.events import CanonicalEvent
from orbit_common.metrics import BATCH_LATENCY, E2E_LATENCY, EVENTS_DROPPED, EVENTS_IN, EVENTS_OUT

from orbit_state import SERVICE
from orbit_state.application.ports import StateStore, StateWrite, SummaryWrite, WriteBatch
from orbit_state.domain.state import LastSeen, is_stale, state_message, state_value, vehicle_state
from orbit_state.domain.summary import FleetTracker, summary_hash, summary_message
from orbit_state.domain.throttle import PublishThrottle

_E2E = E2E_LATENCY.labels(SERVICE)  # bound once: labels() per event costs ~3 us at 100K/s


@dataclass(slots=True)
class BatchStats:
    consumed: int = 0
    written: int = 0
    stale: int = 0
    published: int = 0


class StateService:
    def __init__(
        self,
        store: StateStore,
        *,
        tracker: FleetTracker | None = None,
        throttle: PublishThrottle | None = None,
        topic: str = "telemetry.canonical",
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self.tracker = tracker or FleetTracker()
        self._throttle = throttle or PublishThrottle(2.0)
        self._topic = topic
        self._clock = clock or (lambda: datetime.now(UTC))
        self._mono = monotonic
        self._last: dict[str, LastSeen] = {}

    async def process_batch(self, events: Sequence[CanonicalEvent]) -> BatchStats:
        started = time.perf_counter()
        stats = BatchStats(consumed=len(events))
        EVENTS_IN.labels(SERVICE, self._topic).inc(len(events))
        unknown = {e.vin for e in events if e.vin not in self._last}
        if unknown:
            self._last.update(await self._store.fetch_last_seen(unknown))
        now = self._clock()
        writes: dict[str, StateWrite] = {}  # coalesce per VIN: last write wins within a batch
        for event in events:
            if is_stale(event, self._last.get(event.vin)):
                stats.stale += 1
                continue
            self._last[event.vin] = LastSeen(event.seq, event.ts)
            self.tracker.observe(event)
            state = vehicle_state(event)
            publish = None
            prev = writes.get(event.vin)
            if prev is not None and prev.publish is not None:
                publish = state_message(state)  # already granted a slot this batch: carry the freshest state
            elif self._throttle.allow(event.vin, self._mono()):
                publish = state_message(state)
            writes[event.vin] = StateWrite(
                event.vin, event.tenant_id, event.lat, event.lon, state_value(state), publish
            )
            _E2E.observe(max(0.0, (now - event.ts).total_seconds()))
        batch = WriteBatch(states=list(writes.values()))
        if batch.states:
            await self._store.write(batch)
        stats.written = len(batch.states)
        stats.published = sum(1 for w in batch.states if w.publish is not None)
        if stats.stale:
            EVENTS_DROPPED.labels(SERVICE, "stale").inc(stats.stale)
        EVENTS_OUT.labels(SERVICE, "redis").inc(stats.written)
        BATCH_LATENCY.labels(SERVICE).observe(time.perf_counter() - started)
        return stats

    async def publish_summaries(self) -> int:
        now = self._clock()
        batch = WriteBatch()
        for tenant_id in self.tracker.tenants():
            summary = self.tracker.summary(tenant_id, now)
            batch.summaries.append(SummaryWrite(tenant_id, summary_hash(summary), summary_message(summary)))
        if batch.summaries:
            await self._store.write(batch)
        return len(batch.summaries)
