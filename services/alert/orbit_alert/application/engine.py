"""AlertEngine: builds rule contexts from per-VIN history + lookups, evaluates rules, dedupes and fans out."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from orbit_common.events import CanonicalEvent
from orbit_common.metrics import BATCH_LATENCY, E2E_LATENCY, EVENTS_DROPPED, EVENTS_IN, EVENTS_OUT

from orbit_alert import SERVICE
from orbit_alert.application.ports import (
    AlertDeduper,
    AlertPublisher,
    AlertRepository,
    ChargerIndex,
    EventBus,
    GeoQuery,
    SocSample,
    VehicleInfo,
    VehicleLookup,
)
from orbit_alert.domain.alerts import Alert
from orbit_alert.domain.history import ChargeSnapshot, VinHistory
from orbit_alert.domain.rules import STRAND_SOC_PCT, RuleContext, evaluate, remaining_range_km

_E2E = E2E_LATENCY.labels(SERVICE)  # bound once: labels() per event costs ~3 us at 100K/s

log: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(slots=True)
class BatchStats:
    consumed: int = 0
    candidates: int = 0
    suppressed: int = 0
    emitted: int = 0
    persisted: int = 0
    unknown_vehicle: int = 0
    by_type: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class _Pending:
    event: CanonicalEvent
    ctx: RuleContext
    geo_index: int | None = None


class VehicleCache:
    """vin -> VehicleInfo, filled lazily in chunks from the lookup port; misses are re-tried after a TTL."""

    def __init__(
        self,
        lookup: VehicleLookup,
        *,
        chunk: int = 5000,
        negative_ttl_s: float = 300.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lookup = lookup
        self._chunk = chunk
        self._neg_ttl = negative_ttl_s
        self._mono = monotonic
        self._known: dict[str, VehicleInfo] = {}
        self._missing: dict[str, float] = {}

    def get(self, vin: str) -> VehicleInfo | None:
        return self._known.get(vin)

    async def warm(self, vins: set[str]) -> None:
        now = self._mono()
        todo = [
            v for v in vins if v not in self._known and now - self._missing.get(v, -1e18) >= self._neg_ttl
        ]
        for i in range(0, len(todo), self._chunk):
            part = todo[i : i + self._chunk]
            found = await self._lookup.lookup(part)
            self._known.update(found)
            for v in part:
                if v in found:
                    self._missing.pop(v, None)
                else:
                    self._missing[v] = now

    def __len__(self) -> int:
        return len(self._known)


class AlertEngine:
    def __init__(
        self,
        vehicles: VehicleCache,
        chargers: ChargerIndex,
        deduper: AlertDeduper,
        repo: AlertRepository,
        publisher: AlertPublisher,
        bus: EventBus,
        *,
        topic: str = "telemetry.canonical",
        default_nominal_range_km: float = 300.0,
        soc_bucket_s: int = 300,
        soc_window_s: int = 7200,
        clock: Callable[[], datetime] | None = None,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._vehicles = vehicles
        self._chargers = chargers
        self._deduper = deduper
        self._repo = repo
        self._publisher = publisher
        self._bus = bus
        self._topic = topic
        self._default_range = default_nominal_range_km
        self._bucket_s = soc_bucket_s
        self._window_s = soc_window_s
        self._clock = clock or (lambda: datetime.now(UTC))
        self._log = logger or log
        self._history: dict[str, VinHistory] = {}

    def history(self, vin: str) -> VinHistory:
        h = self._history.get(vin)
        if h is None:
            h = self._history[vin] = VinHistory(bucket_s=self._bucket_s)
        return h

    # -- context construction -------------------------------------------------------------------
    def _build(self, e: CanonicalEvent, soc_samples: list[SocSample]) -> _Pending:
        h = self.history(e.vin)
        ts = e.ts.timestamp()
        ctx = RuleContext()
        info = self._vehicles.get(e.vin)
        ctx.nominal_range_km = (
            info.nominal_range_km if info and info.nominal_range_km else None
        ) or self._default_range
        if e.soc_pct is not None:
            ctx.max_soc_2h = h.soc.max_in_window(ts, self._window_s)
            if h.soc.update(ts, e.soc_pct):
                soc_samples.append(SocSample(e.vin, int(ts // self._bucket_s) * self._bucket_s, e.soc_pct))
        prev = h.charge
        if prev is not None:
            ctx.prev_charging_state, ctx.prev_charging_kw, ctx.prev_ts_epoch = (
                prev.charging_state,
                prev.charging_kw,
                prev.ts_epoch,
            )
        if prev is None or ts >= prev.ts_epoch:
            h.charge = ChargeSnapshot(e.charging_state, e.charging_kw, ts)
        return _Pending(e, ctx)

    async def process_batch(self, events: Sequence[CanonicalEvent]) -> BatchStats:
        started = time.perf_counter()
        stats = BatchStats(consumed=len(events))
        EVENTS_IN.labels(SERVICE, self._topic).inc(len(events))
        if not events:
            return stats
        await self._vehicles.warm({e.vin for e in events})
        soc_samples: list[SocSample] = []
        pending = [self._build(e, soc_samples) for e in events]

        geo: list[GeoQuery] = []
        for p in pending:
            e = p.event
            if e.soc_pct is not None and e.soc_pct < STRAND_SOC_PCT:
                rng = remaining_range_km(e.soc_pct, p.ctx.nominal_range_km) or 0.0
                p.geo_index = len(geo)
                geo.append(GeoQuery(e.lat, e.lon, max(rng, 0.1)))
        if geo:
            reachable = await self._chargers.reachable(geo)
            for p in pending:
                if p.geo_index is not None:
                    p.ctx.reachable_charger = reachable[p.geo_index]

        candidates: list[Alert] = []
        seen: set[str] = set()
        for p in pending:
            for a in evaluate(p.event, p.ctx):
                stats.candidates += 1
                if a.dedupe_key in seen:
                    stats.suppressed += 1
                    continue
                seen.add(a.dedupe_key)
                candidates.append(a)
        alerts: list[Alert] = []
        if candidates:
            won = await self._deduper.claim([a.dedupe_key for a in candidates])
            for a, ok in zip(candidates, won, strict=True):
                if ok:
                    alerts.append(a)
                else:
                    stats.suppressed += 1

        if alerts:
            await self._persist(alerts, stats)
            await self._publisher.emit(alerts)
        await self._bus.publish(alerts, soc_samples)
        now = self._clock()
        for a in alerts:
            stats.emitted += 1
            stats.by_type[a.type] = stats.by_type.get(a.type, 0) + 1
            _E2E.observe(max(0.0, (now - a.ts).total_seconds()))
        EVENTS_OUT.labels(SERVICE, "alerts").inc(stats.emitted)
        if stats.suppressed:
            EVENTS_DROPPED.labels(SERVICE, "deduped").inc(stats.suppressed)
        BATCH_LATENCY.labels(SERVICE).observe(time.perf_counter() - started)
        return stats

    async def _persist(self, alerts: list[Alert], stats: BatchStats) -> None:
        rows: list[Alert] = []
        for a in alerts:
            info = self._vehicles.get(a.vin)
            if info is None:
                stats.unknown_vehicle += 1
                continue
            a.vehicle_id = info.vehicle_id
            rows.append(a)
        if stats.unknown_vehicle:
            EVENTS_DROPPED.labels(SERVICE, "unknown_vehicle").inc(stats.unknown_vehicle)
        if rows:
            ids = await self._repo.insert(rows)
            for a, i in zip(rows, ids, strict=True):
                a.id = i
            stats.persisted = len(rows)
