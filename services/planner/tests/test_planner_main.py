from __future__ import annotations

from datetime import UTC, datetime

import pytest
from orbit_planner.__main__ import main, parse_request


def test_parse_request_accepts_contract_payload() -> None:
    req = parse_request(
        b'{"plan_id": 7, "vin": "1hgcm82633a004352", "vehicle_id": 3, "tenant_id": 1, '
        b'"target_soc": 85, "depart_ts": "2026-10-02T00:30:00Z"}'
    )
    assert req.plan_id == 7 and req.vin == "1HGCM82633A004352" and req.vehicle_id == 3
    assert req.target_soc == 85 and req.depart_ts == datetime(2026, 10, 2, 0, 30, tzinfo=UTC)
    minimal = parse_request(
        b'{"plan_id": 1, "vin": "X", "tenant_id": 2, "depart_ts": "2026-10-02T06:00:00+05:30"}'
    )
    assert minimal.vehicle_id is None and minimal.target_soc == 90
    with pytest.raises(KeyError):
        parse_request(b'{"vin": "X"}')


def test_cli_help_lists_nightly_mode(capsys) -> None:
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
    assert "--nightly" in capsys.readouterr().out


def test_parse_request_accepts_epoch_seconds() -> None:
    req = parse_request(b'{"plan_id": 1, "vin": "X", "tenant_id": 2, "depart_ts": 1790000000}')
    assert req.depart_ts == datetime.fromtimestamp(1790000000, tz=UTC)
