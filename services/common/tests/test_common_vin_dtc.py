import pytest
from hypothesis import given
from hypothesis import strategies as st
from orbit_common import dtc, vin


def test_known_valid_vin():
    assert vin.is_valid("1HGCM82633A004352")


@pytest.mark.parametrize(
    "bad", ["1HGCM82633A00435", "1HGCM82633A0043521", "1HGCM82633I004352", "1HGCM82633A004353"]
)
def test_invalid_vins(bad):
    assert not vin.is_valid(bad)


@given(st.text(alphabet="ABCDEFGHJKLMNPRSTUVWXYZ0123456789", min_size=17, max_size=17))
def test_make_valid_roundtrip(body):
    assert vin.is_valid(vin.make_valid(body))


def test_dtc_parse():
    d = dtc.parse("p0a80")
    assert d and d.code == "P0A80" and d.is_battery and d.system == "Powertrain"
    assert dtc.parse("X1234") is None
    assert dtc.parse("P0A8") is None


def test_dtc_extract_all():
    assert dtc.extract_all("P0301;P0A80,junk U0100") == ["P0301", "P0A80", "U0100"]
