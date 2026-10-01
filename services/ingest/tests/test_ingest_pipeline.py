from datetime import timedelta

import orjson
import pytest
from ingest_fakes import NOW, VIN, FakeProducer, SetDeduper, oem_a, oem_b, rec
from orbit_common.events import CanonicalEvent
from orbit_common.streaming import RollingBloom
from orbit_ingest.application.pipeline import IngestPipeline
from orbit_ingest.domain.processing import REASON_DUPLICATE, REASON_FUTURE_TS, REASON_MALFORMED


def make_pipeline(producer: FakeProducer, dedupe=None) -> IngestPipeline:
    return IngestPipeline(producer, dedupe or SetDeduper(), clock=lambda: NOW)


async def test_batch_routes_canonical_dlq_and_duplicates(producer: FakeProducer):
    p = make_pipeline(producer)
    future_ts = (NOW + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    records = [
        rec(oem_a(seq=1), 0),
        rec(oem_b(seq=2), 1),
        rec(oem_a(seq=1), 2),  # duplicate (vin, seq)
        rec(b"garbage", 3),
        rec(oem_a(seq=3, ts=future_ts), 4),
        rec({"oem": "OEM_Q"}, 5),
    ]
    stats = await p.process_batch(records)
    assert stats.consumed == 6 and stats.produced == 2 and stats.duplicates == 1 and stats.dlq == 3
    assert stats.reasons[REASON_DUPLICATE] == 1 and stats.reasons[REASON_MALFORMED] == 1
    assert stats.reasons[REASON_FUTURE_TS] == 1
    canon = producer.sent["telemetry.canonical"]
    assert [k for k, _ in canon] == [VIN.encode(), VIN.encode()]
    events = [CanonicalEvent.from_json(v) for _, v in canon]
    assert [e.seq for e in events] == [1, 2] and events[1].oem == "OEM_B"
    dlq = [orjson.loads(v) for _, v in producer.sent["telemetry.dlq"]]
    assert {d["reason"] for d in dlq} == {REASON_MALFORMED, REASON_FUTURE_TS, "unknown_oem"}
    assert all("raw" in d for d in dlq)


async def test_dedupe_with_rolling_bloom(producer: FakeProducer):
    p = make_pipeline(producer, RollingBloom(10_000, window_s=1e9))
    batch = [rec(oem_a(seq=i), i) for i in range(200)] + [
        rec(oem_a(seq=i), 200 + i) for i in range(0, 200, 20)
    ]
    stats = await p.process_batch(batch)
    assert stats.produced == 200 and stats.duplicates == 10
    # a second batch with the same keys is entirely duplicate
    stats2 = await p.process_batch([rec(oem_a(seq=i), i) for i in range(200)])
    assert stats2.produced == 0 and stats2.duplicates == 200


async def test_dtc_counts_only_accepted_events(producer: FakeProducer):
    p = make_pipeline(producer)
    await p.process_batch([rec(oem_b(seq=1)), rec(oem_b(seq=1)), rec(oem_a(seq=2, dtc=["P0A80"]))])
    assert p.dtc.estimate("P0A80") == 2 and p.dtc.estimate("P0301") == 1


async def test_send_failure_propagates_so_offsets_are_not_committed():
    producer = FakeProducer(fail_topics={"telemetry.canonical"})
    p = make_pipeline(producer)
    with pytest.raises(RuntimeError):
        await p.process_batch([rec(oem_a(seq=1))])


async def test_empty_value_goes_to_dlq(producer: FakeProducer):
    p = make_pipeline(producer)
    stats = await p.process_batch([rec(b"")])
    assert stats.dlq == 1 and "telemetry.canonical" not in producer.sent
