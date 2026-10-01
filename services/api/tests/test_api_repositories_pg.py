"""Exercise every asyncpg repository method against a recording stub connection: proves the SQL is well-formed
Python (placeholders, argument counts) and the row mapping works. Real SQL runs in tests/integration."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest
from orbit_api.repositories.postgres import all_chargers, pg_repos

NOW = datetime(2026, 10, 1, tzinfo=UTC)


class Record(dict):
    pass


class StubConn:
    def __init__(self, rows: list[dict[str, Any]] | None = None, value: Any = 1) -> None:
        self.rows = [Record(r) for r in (rows or [])]
        self.value = value
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _check(self, sql: str, args: tuple[Any, ...]) -> None:
        self.calls.append((sql, args))
        placeholders = {int(n) for n in re.findall(r"\$(\d+)", sql)}
        assert placeholders == set(range(1, len(args) + 1)), (sorted(placeholders), len(args), sql)

    async def fetch(self, sql: str, *args: Any) -> list[Record]:
        self._check(sql, args)
        return self.rows

    async def fetchrow(self, sql: str, *args: Any) -> Record | None:
        self._check(sql, args)
        return self.rows[0] if self.rows else None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self._check(sql, args)
        return self.value

    async def execute(self, sql: str, *args: Any) -> str:
        self._check(sql, args)
        return "OK"


@pytest.fixture
def conn():
    return StubConn(
        rows=[
            {
                "id": 1,
                "vin": "X",
                "lat": 1.0,
                "lon": 2.0,
                "name": "t",
                "timezone": "UTC",
                "acked_by": "u",
                "acked_ts": NOW,
                "status": "APPROVED",
            }
        ],
        value=7,
    )


async def test_every_repository_method(conn):
    r = pg_repos(conn, 1)
    assert (await r.fleets.find())[0]["id"] == 1
    assert await r.fleets.vins(1) == {"X"}
    assert await r.vehicles.find(fleet_id=None, after_id=None, limit=5)
    assert (await r.vehicles.get_by_vin("X"))["vin"] == "X"
    assert await r.vehicles.count() == 7
    assert await r.vehicles.depot_location(1) == (1.0, 2.0)
    assert await r.vehicles.telemetry("X", from_ts=None, to_ts=NOW, cursor=(NOW, 5), limit=2)
    assert await r.vehicles.telemetry("X", from_ts=None, to_ts=None, cursor=None, limit=2)
    assert await r.plans.create(vehicle_id=1, target_soc=80, depart_ts=NOW, requested_by="u") == 7
    assert await r.plans.get(1) and await r.plans.slots(1)
    assert await r.plans.find(vin="X", status=None, cursor=(NOW, 1), limit=1)
    assert await r.alerts.find(severity="WARN", since=NOW, acked=False, cursor=None, limit=1)
    assert (await r.alerts.ack(1, "u"))["acked_by"] == "u"
    assert await r.alerts.count_open() == 7
    assert await r.chargers.find(depot_id=1, public=None, after_id=None, limit=1)
    assert (await r.tariffs.current())["tariff_id"] == 1
    assert await r.reports.cost_by_day(NOW, NOW)
    assert await r.reports.battery_health(after_vin="A", limit=1)
    assert (await r.agent.decide(1, status="APPROVED", decided_by="u"))["status"] == "APPROVED"
    assert await r.agent.get(1)
    assert await r.privacy.erase_driver(1) is True
    assert await r.audit.insert(actor_id="u", action="A", resource="r", details={}) == 7
    assert await r.audit.find(cursor=(NOW, 1), limit=1)
    assert await all_chargers(conn)
    assert len(conn.calls) >= 25


async def test_empty_results():
    conn = StubConn(rows=[], value=0)
    r = pg_repos(conn, 1)
    assert await r.vehicles.get_by_vin("X") is None
    assert await r.vehicles.depot_location(1) is None
    assert await r.plans.get(1) is None
    assert await r.alerts.ack(1, "u") is None
    assert await r.tariffs.current() is None
    assert await r.agent.decide(1, status="REJECTED", decided_by="u") is None
    assert await r.agent.get(1) is None
    assert await r.privacy.erase_driver(1) is False
    assert await r.fleets.vins(1) == set()
    assert await r.fleets.find() == []
