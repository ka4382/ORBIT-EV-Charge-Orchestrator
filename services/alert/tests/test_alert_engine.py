# ruff: noqa: ASYNC110 - tests poll fakes with short sleeps on purpose
import asyncio

from alert_fakes import (
    NOW,
    UNKNOWN_VIN,
    VIN,
    VIN2,
    FakeBus,
    FakeChargers,
    FakeDeduper,
    FakeLookup,
    FakePublisher,
    FakeRepo,
    ev,
)
from orbit_alert.application.engine import AlertEngine, VehicleCache
from orbit_alert.application.ports import VehicleInfo
from orbit_alert.application.runner import consume, decode_records
from orbit_alert.domain.alerts import BATTERY_DTC, CHARGE_INTERRUPTED, LOW_SOC, SOC_DROP, STRAND_RISK


def build(reachable: bool = True, lookup: FakeLookup | None = None):
    lookup = lookup or FakeLookup()
    chargers, dedupe, repo, pub, bus = (
        FakeChargers(reachable),
        FakeDeduper(),
        FakeRepo(),
        FakePublisher(),
        FakeBus(),
    )
    engine = AlertEngine(VehicleCache(lookup, chunk=1), chargers, dedupe, repo, pub, bus, clock=lambda: NOW)
    return engine, lookup, chargers, dedupe, repo, pub, bus


async def test_strand_risk_end_to_end_with_lookup_and_geosearch():
    engine, lookup, chargers, dedupe, repo, pub, bus = build(reachable=False)
    stats = await engine.process_batch([ev(seq=1, soc_pct=10.0), ev(seq=1, vin=VIN2, soc_pct=10.0)])
    assert stats.consumed == 2 and stats.emitted == 4 and stats.persisted == 4
    assert sorted(lookup.calls) == [[VIN], [VIN2]]  # chunked lookups
    assert [q.radius_km for q in chargers.queries] == [30.0, 30.0]  # 10% of nominal 300 km (default for VIN2)
    types = sorted((a.vin, a.type) for a in pub.emitted)
    assert types == sorted([(VIN, STRAND_RISK), (VIN, LOW_SOC), (VIN2, STRAND_RISK), (VIN2, LOW_SOC)])
    assert all(a.id is not None and a.vehicle_id in (1, 2) for a in pub.emitted)
    assert len(bus.alerts) == 4 and len(bus.samples) == 2  # first SoC bucket per VIN mirrored


async def test_reachable_charger_suppresses_strand_risk():
    engine, *_rest, pub, _bus = build(reachable=True)
    await engine.process_batch([ev(seq=1, soc_pct=10.0)])
    assert [a.type for a in pub.emitted] == [LOW_SOC]


async def test_dedupe_within_batch_and_across_batches():
    engine, _l, _c, dedupe, repo, pub, _b = build()
    stats = await engine.process_batch([ev(seq=1, soc_pct=18.0), ev(seq=2, dt_s=1, soc_pct=17.0)])
    assert stats.candidates == 2 and stats.suppressed == 1 and stats.emitted == 1
    stats2 = await engine.process_batch([ev(seq=3, dt_s=2, soc_pct=16.0)])
    assert stats2.suppressed == 1 and stats2.emitted == 0
    assert len(repo.rows) == 1 and dedupe.claimed == {f"alert:dedupe:{VIN}:LOW_SOC"}


async def test_unknown_vehicle_is_emitted_but_not_persisted():
    engine, lookup, _c, _d, repo, pub, bus = build(reachable=False)
    stats = await engine.process_batch([ev(seq=1, vin=UNKNOWN_VIN, soc_pct=5.0)])
    assert stats.unknown_vehicle == 2 and stats.persisted == 0 and stats.emitted == 2
    assert repo.rows == [] and all(a.id is None for a in pub.emitted) and len(bus.alerts) == 2
    # negative cache: the second batch must not hit the lookup again
    await engine.process_batch([ev(seq=2, dt_s=1, vin=UNKNOWN_VIN, soc_pct=5.0)])
    assert lookup.calls == [[UNKNOWN_VIN]]


async def test_vehicle_cache_retries_after_negative_ttl():
    clock = [0.0]
    lookup = FakeLookup(table={})
    cache = VehicleCache(lookup, negative_ttl_s=10.0, monotonic=lambda: clock[0])
    await cache.warm({VIN})
    await cache.warm({VIN})
    assert len(lookup.calls) == 1
    clock[0] = 11.0
    lookup.table[VIN] = VehicleInfo(9, 100.0)
    await cache.warm({VIN})
    assert len(lookup.calls) == 2 and cache.get(VIN).vehicle_id == 9 and len(cache) == 1
    await cache.warm({VIN})
    assert len(lookup.calls) == 2


async def test_soc_drop_uses_ring_history():
    engine, *_rest, pub, bus = build()
    await engine.process_batch([ev(seq=1, soc_pct=90.0)])
    await engine.process_batch([ev(seq=2, dt_s=3600, soc_pct=45.0)])  # drop of 45 within 2 h
    assert [a.type for a in pub.emitted] == [SOC_DROP]
    assert pub.emitted[0].payload["from_soc_pct"] == 90.0
    assert [s.soc for s in bus.samples] == [90.0, 45.0]
    await engine.process_batch(
        [ev(seq=3, dt_s=3700, soc_pct=44.0)]
    )  # same bucket -> no mirror, deduped alert
    assert len(bus.samples) == 2 and len(pub.emitted) == 1


async def test_charge_interrupted_and_battery_dtc_flow():
    engine, *_rest, pub, _bus = build()
    await engine.process_batch([ev(seq=1, charging_state="DC", charging_kw=50.0, soc_pct=40.0)])
    await engine.process_batch([ev(seq=2, dt_s=20, charging_state="NONE", soc_pct=41.0, dtc=["P0A80"])])
    assert sorted(a.type for a in pub.emitted) == [BATTERY_DTC, CHARGE_INTERRUPTED]


async def test_out_of_order_event_does_not_regress_charge_snapshot():
    engine, *_rest, pub, _bus = build()
    await engine.process_batch([ev(seq=2, dt_s=10, charging_state="DC", charging_kw=50.0, soc_pct=40.0)])
    await engine.process_batch(
        [ev(seq=1, dt_s=0, charging_state="NONE", soc_pct=40.0)]
    )  # older than snapshot
    assert engine.history(VIN).charge.charging_state == "DC"
    assert pub.emitted == []  # gap check uses prev ts; old event does not interrupt


async def test_empty_batch_is_noop():
    engine, lookup, *_rest = build()
    stats = await engine.process_batch([])
    assert stats.consumed == 0 and lookup.calls == []


async def test_consume_loop_commits_after_engine():
    from aiokafka.structs import TopicPartition

    tp = TopicPartition("telemetry.canonical", 0)

    class R:
        def __init__(self, v, off):
            self.value, self.offset = v, off

    class Consumer:
        def __init__(self):
            self.batches = [{tp: [R(ev(seq=1, soc_pct=10.0).to_json(), 4), R(b"bad", 5)]}]
            self.commits = []
            self.owned = {tp for b in self.batches for tp in b}

        async def getmany(self, *, timeout_ms, max_records):
            await asyncio.sleep(0)
            return self.batches.pop(0) if self.batches else {}

        def assignment(self):

            return self.owned

        async def commit(self, offsets):
            self.commits.append(offsets)

    engine, *_rest, pub, _bus = build(reachable=False)
    consumer = Consumer()
    stop = asyncio.Event()

    async def stopper():
        while consumer.batches:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)
        stop.set()

    await asyncio.gather(consume(consumer, engine, stop), stopper())
    assert consumer.commits == [{tp: 6}] and len(pub.emitted) == 2
    assert len(decode_records([R(b"x", 0)])) == 0
