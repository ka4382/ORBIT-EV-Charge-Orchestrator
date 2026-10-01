import pytest
from orbit_common.adapters import UnknownOemError, normalise

A = {
    "oem": "OEM_A",
    "vin": "1HGCM82633A004352",
    "ts": "2026-09-25T10:15:02.120Z",
    "seq": 88412,
    "tenant": 12,
    "lat": 21.1702,
    "lon": 72.8311,
    "speed_kmh": 64.2,
    "soc": 41.0,
    "soh": 93.5,
    "odo_km": 18234.7,
    "amb_c": 31.2,
    "dtc": ["P0301"],
    "evt": "HARSH_BRAKE",
    "chg": {"st": "NONE", "kw": 0},
}
B = {
    "oem": "OEM_B",
    "vehicle": {"vin": "1HGCM82633A004352", "tenant_id": 12},
    "t": 1758795302120,
    "n": 88412,
    "pos": {"latitude": 21.1702, "longitude": 72.8311},
    "velocity_mph": 39.9,
    "battery": {"level": 0.41, "health": 0.935, "charge_mode": 2, "charge_kw": 48.5},
    "odometer_mi": 11330.5,
    "temp_f": 88.2,
    "faults": "P0301;",
    "event_code": "HB",
}


def test_oem_a():
    e = normalise(A)
    assert e.vin == "1HGCM82633A004352" and e.seq == 88412 and e.tenant_id == 12
    assert e.dtc == ["P0301"] and e.evt == "HARSH_BRAKE" and e.charging_state == "NONE"


def test_oem_b_units_and_codes():
    e = normalise(B)
    assert e.speed_kmh == pytest.approx(64.21, abs=0.01)
    assert e.soc_pct == pytest.approx(41.0) and e.soh_pct == pytest.approx(93.5)
    assert e.ambient_c == pytest.approx(31.2, abs=0.05)
    assert e.odo_km == pytest.approx(18234.7, abs=0.5)
    assert e.charging_state == "DC" and e.charging_kw == 48.5
    assert e.dtc == ["P0301"] and e.evt == "HARSH_BRAKE"


def test_unknown_oem():
    with pytest.raises(UnknownOemError):
        normalise({"oem": "OEM_Z"})


def test_invalid_vin_rejected():
    with pytest.raises(ValueError):
        normalise({**A, "vin": "1HGCM82633A004353"})


def test_json_roundtrip():
    e = normalise(A)
    assert type(e).from_json(e.to_json()) == e
