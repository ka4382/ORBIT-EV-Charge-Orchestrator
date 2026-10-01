from datetime import timedelta

import orjson
from ingest_fakes import NOW, VIN, oem_a, oem_b
from orbit_ingest.domain.processing import (
    REASON_FUTURE_TS,
    REASON_INVALID,
    REASON_MALFORMED,
    REASON_UNKNOWN_OEM,
    Accepted,
    DtcTracker,
    Rejected,
    decode,
)


def test_decode_oem_a_and_b():
    a = decode(orjson.dumps(oem_a()), NOW)
    b = decode(orjson.dumps(oem_b()), NOW)
    assert isinstance(a, Accepted) and isinstance(b, Accepted)
    assert a.event.vin == VIN and b.event.charging_state == "DC"
    assert b.event.dtc == ["P0301", "P0A80"]


def test_decode_malformed_json():
    out = decode(b"{not json", NOW)
    assert isinstance(out, Rejected) and out.reason == REASON_MALFORMED
    env = orjson.loads(out.dlq_payload())
    assert env["reason"] == REASON_MALFORMED and env["raw"] == "{not json"


def test_decode_non_object():
    out = decode(b"[1,2,3]", NOW)
    assert isinstance(out, Rejected) and out.reason == REASON_MALFORMED


def test_decode_unknown_oem():
    out = decode(orjson.dumps({"oem": "OEM_Z", "vin": VIN}), NOW)
    assert isinstance(out, Rejected) and out.reason == REASON_UNKNOWN_OEM


def test_decode_missing_field_is_invalid():
    d = oem_a()
    del d["lat"]
    out = decode(orjson.dumps(d), NOW)
    assert isinstance(out, Rejected) and out.reason == REASON_INVALID and "KeyError" in out.detail


def test_decode_bad_vin_is_invalid():
    out = decode(orjson.dumps(oem_a(vin="1HGCM82633A004353")), NOW)
    assert isinstance(out, Rejected) and out.reason == REASON_INVALID


def test_decode_bad_type_is_invalid():
    out = decode(orjson.dumps(oem_a(chg="oops")), NOW)
    assert isinstance(out, Rejected) and out.reason == REASON_INVALID


def test_decode_invalid_utf8_raw_survives_dlq_envelope():
    out = decode(b"\xff\xfe{", NOW)
    assert isinstance(out, Rejected)
    assert orjson.loads(out.dlq_payload())["reason"] == REASON_MALFORMED


def test_future_ts_rejected_beyond_5_min():
    future = (NOW + timedelta(minutes=6)).isoformat().replace("+00:00", "Z")
    out = decode(orjson.dumps(oem_a(ts=future)), NOW)
    assert isinstance(out, Rejected) and out.reason == REASON_FUTURE_TS
    ok = (NOW + timedelta(minutes=4)).isoformat().replace("+00:00", "Z")
    assert isinstance(decode(orjson.dumps(oem_a(ts=ok)), NOW), Accepted)


def test_dtc_tracker_topk():
    t = DtcTracker(k=2)
    for _ in range(10):
        t.record(["P0301", "P0A80"])
    t.record(["U0100"])
    top = dict(t.top())
    assert top["P0301"] == 10 and top["P0A80"] == 10 and "U0100" not in top
    assert t.estimate("U0100") >= 1
