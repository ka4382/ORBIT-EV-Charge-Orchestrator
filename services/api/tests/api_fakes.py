"""In-memory repository adapters implementing orbit_api.repositories.base ports, plus a fake publisher."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from orbit_api.repositories.base import Repos, Row

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
VIN_T1 = ["1HGCM82633A004352", "5YJ3E1EA7KF317000", "WBA3A5C50CF256789"]
VIN_T2 = ["JH4KA7561PC008269", "2T1BURHE0FC311111", "KMHDU4AD8AU765432"]


@dataclass
class FakeData:
    vehicles: list[Row] = field(default_factory=list)
    fleets: list[Row] = field(default_factory=list)
    depots: dict[int, tuple[float, float]] = field(default_factory=dict)
    telemetry: list[Row] = field(default_factory=list)
    plans: list[Row] = field(default_factory=list)
    slots: list[Row] = field(default_factory=list)
    alerts: list[Row] = field(default_factory=list)
    chargers: list[Row] = field(default_factory=list)
    tariffs: list[Row] = field(default_factory=list)
    sessions: list[Row] = field(default_factory=list)
    battery: list[Row] = field(default_factory=list)
    agent_actions: list[Row] = field(default_factory=list)
    drivers: list[Row] = field(default_factory=list)
    assignments: list[Row] = field(default_factory=list)
    audit: list[Row] = field(default_factory=list)
    ids: itertools.count = field(default_factory=lambda: itertools.count(1000))

    def next_id(self) -> int:
        return next(self.ids)


def seed() -> FakeData:
    d = FakeData()
    d.depots = {1: (13.0827, 80.2707), 2: (12.9716, 77.5946)}
    d.fleets = [
        {"id": 1, "tenant_id": 1, "name": "Chennai Fleet 1", "depot_id": 1},
        {"id": 2, "tenant_id": 2, "name": "Bengaluru Fleet 1", "depot_id": 2},
    ]
    for i, vin in enumerate(VIN_T1 + VIN_T2):
        tenant = 1 if i < 3 else 2
        d.vehicles.append(
            {
                "id": i + 1,
                "vin": vin,
                "plate": f"TN{i:05d}",
                "fleet_id": tenant,
                "tenant_id": tenant,
                "created_at": NOW,
                "model": "A-Van 60",
                "powertrain": "BEV",
                "battery_capacity_kwh": 60.0,
                "nominal_range_km": 320.0,
                "oem": "OEM_A",
            }
        )
    for n in range(120):
        d.telemetry.append(
            {
                "ts": NOW - timedelta(seconds=n),
                "vin": VIN_T1[0],
                "seq": 1000 - n,
                "lat": 13.08271,
                "lon": 80.27071,
                "speed_kmh": 40.0,
                "soc_pct": 50.0,
                "soh_pct": 95.0,
                "odo_km": 100.0,
                "ambient_c": 30.0,
                "charging_state": 0,
                "charging_kw": 0.0,
                "dtc": [],
                "evt": None,
                "tenant_id": 1,
            }
        )
    for n in range(7):
        d.alerts.append(
            {
                "id": n + 1,
                "tenant_id": 1 if n < 5 else 2,
                "vin": VIN_T1[n % 3] if n < 5 else VIN_T2[0],
                "type": "LOW_SOC",
                "severity": "WARN" if n % 2 else "CRITICAL",
                "ts": NOW - timedelta(minutes=n),
                "payload": {"soc": 12},
                "acked_by": None,
                "acked_ts": None,
            }
        )
    d.chargers = [
        {
            "id": 1,
            "tenant_id": 1,
            "depot_id": 1,
            "name": "D1-C1",
            "lat": 13.083,
            "lon": 80.271,
            "max_kw": 22.0,
            "connector": "Type2",
            "is_public": False,
            "price_per_kwh": None,
        },
        {
            "id": 2,
            "tenant_id": None,
            "depot_id": None,
            "name": "Chennai Public 1",
            "lat": 13.10,
            "lon": 80.30,
            "max_kw": 120.0,
            "connector": "CCS2",
            "is_public": True,
            "price_per_kwh": 16.0,
        },
        {
            "id": 3,
            "tenant_id": 2,
            "depot_id": 2,
            "name": "D2-C1",
            "lat": 13.09,
            "lon": 80.28,
            "max_kw": 60.0,
            "connector": "CCS2",
            "is_public": False,
            "price_per_kwh": None,
        },
        {
            "id": 4,
            "tenant_id": None,
            "depot_id": None,
            "name": "Far Public",
            "lat": 14.5,
            "lon": 81.5,
            "max_kw": 150.0,
            "connector": "CCS2",
            "is_public": True,
            "price_per_kwh": 18.0,
        },
    ]
    d.tariffs = [
        {
            "tenant_id": 1,
            "tariff_id": 1,
            "name": "TOU-Commercial",
            "timezone": "Asia/Kolkata",
            "slots": [
                {"dow": 0, "start_min": 0, "end_min": 360, "price_per_kwh": 6.5},
                {"dow": 0, "start_min": 1080, "end_min": 1320, "price_per_kwh": 12.5},
            ],
        }
    ]
    d.sessions = [
        {
            "tenant_id": 1,
            "start_ts": NOW - timedelta(days=1),
            "end_ts": NOW - timedelta(hours=22),
            "kwh": 40.0,
            "cost": 260.0,
        },
        {
            "tenant_id": 1,
            "start_ts": NOW - timedelta(days=1, hours=3),
            "end_ts": NOW - timedelta(days=1),
            "kwh": 20.0,
            "cost": None,
        },
    ]
    d.battery = [
        {
            "tenant_id": 1,
            "vin": VIN_T1[0],
            "soh_pct": 93.5,
            "cycles": 210,
            "trend_30d": -0.4,
            "similar": [VIN_T1[1]],
        },
        {"tenant_id": 1, "vin": VIN_T1[1], "soh_pct": 97.0, "cycles": 80, "trend_30d": None, "similar": []},
    ]
    d.agent_actions = [
        {
            "id": 1,
            "tenant_id": 1,
            "action": "REPLAN",
            "params": {"vins": VIN_T1[:1]},
            "rationale": "cheap window",
            "status": "PENDING",
            "decided_by": None,
            "decided_ts": None,
        },
        {
            "id": 2,
            "tenant_id": 1,
            "action": "REPLAN",
            "params": {},
            "rationale": "x",
            "status": "APPROVED",
            "decided_by": "u",
            "decided_ts": NOW,
        },
    ]
    d.drivers = [
        {"id": 1, "tenant_id": 1, "name_masked": "R*** S***", "licence_hash": "abc", "erased_at": None},
        {"id": 2, "tenant_id": 2, "name_masked": "A*** B***", "licence_hash": "def", "erased_at": None},
    ]
    d.assignments = [{"id": 1, "driver_id": 1, "vehicle_id": 1}]
    return d


class _Base:
    def __init__(self, data: FakeData, tenant_id: int) -> None:
        self.d = data
        self.t = tenant_id

    def _mine(self, rows: list[Row]) -> list[Row]:
        return [r for r in rows if r.get("tenant_id") == self.t]


class FakeFleetRepository(_Base):
    async def find(self) -> list[Row]:
        out = []
        for f in self._mine(self.d.fleets):
            count = sum(1 for v in self.d.vehicles if v["fleet_id"] == f["id"] and v["tenant_id"] == self.t)
            out.append({**f, "vehicle_count": count})
        return out

    async def vins(self, fleet_id: int) -> set[str]:
        return {v["vin"] for v in self._mine(self.d.vehicles) if v["fleet_id"] == fleet_id}


class FakeVehicleRepository(_Base):
    async def find(self, *, fleet_id: int | None, after_id: int | None, limit: int) -> list[Row]:
        rows = [
            v
            for v in self._mine(self.d.vehicles)
            if (fleet_id is None or v["fleet_id"] == fleet_id) and (after_id is None or v["id"] > after_id)
        ]
        return sorted(rows, key=lambda r: r["id"])[:limit]

    async def get_by_vin(self, vin: str) -> Row | None:
        return next((v for v in self._mine(self.d.vehicles) if v["vin"] == vin), None)

    async def count(self) -> int:
        return len(self._mine(self.d.vehicles))

    async def find_by_vins(self, vins: Sequence[str]) -> list[Row]:
        wanted = set(vins)
        return [{"id": v["id"], "vin": v["vin"]} for v in self._mine(self.d.vehicles) if v["vin"] in wanted]

    async def depot_location(self, vehicle_id: int) -> tuple[float, float] | None:
        v = next((v for v in self._mine(self.d.vehicles) if v["id"] == vehicle_id), None)
        if v is None:
            return None
        fleet = next((f for f in self.d.fleets if f["id"] == v["fleet_id"]), None)
        return self.d.depots.get(fleet["depot_id"]) if fleet else None

    async def telemetry(
        self,
        vin: str,
        *,
        from_ts: datetime | None,
        to_ts: datetime | None,
        cursor: tuple[datetime, int] | None,
        limit: int,
    ) -> list[Row]:
        rows = [
            r
            for r in self._mine(self.d.telemetry)
            if r["vin"] == vin
            and (from_ts is None or r["ts"] >= from_ts)
            and (to_ts is None or r["ts"] <= to_ts)
            and (cursor is None or (r["ts"], r["seq"]) < cursor)
        ]
        return sorted(rows, key=lambda r: (r["ts"], r["seq"]), reverse=True)[:limit]


class FakePlanRepository(_Base):
    async def totals_today(self) -> tuple[float, float]:
        plans = self._mine(self.d.plans)
        ready = [p for p in plans if p.get("status") in ("READY", "ACTIVE", "DONE") and p.get("total_cost")]
        cost = sum(float(p["total_cost"]) for p in ready)
        return cost, sum(float(p.get("baseline_cost") or 0) - float(p["total_cost"]) for p in ready)

    async def create(
        self, *, vehicle_id: int, target_soc: float, depart_ts: datetime, requested_by: str
    ) -> int:
        pid = self.d.next_id()
        vin = next(v["vin"] for v in self.d.vehicles if v["id"] == vehicle_id)
        self.d.plans.append(
            {
                "id": pid,
                "tenant_id": self.t,
                "vehicle_id": vehicle_id,
                "vin": vin,
                "status": "PENDING",
                "target_soc": target_soc,
                "depart_ts": depart_ts,
                "created_ts": datetime.now(UTC),
                "total_cost": None,
                "baseline_cost": None,
                "savings": None,
                "requested_by": requested_by,
            }
        )
        return pid

    async def get(self, plan_id: int) -> Row | None:
        return next((p for p in self._mine(self.d.plans) if p["id"] == plan_id), None)

    async def slots(self, plan_id: int) -> list[Row]:
        return [s for s in self.d.slots if s["plan_id"] == plan_id]

    async def find(
        self, *, vin: str | None, status: str | None, cursor: tuple[datetime, int] | None, limit: int
    ) -> list[Row]:
        rows = [
            p
            for p in self._mine(self.d.plans)
            if (vin is None or p["vin"] == vin)
            and (status is None or p["status"] == status)
            and (cursor is None or (p["created_ts"], p["id"]) < cursor)
        ]
        return sorted(rows, key=lambda r: (r["created_ts"], r["id"]), reverse=True)[:limit]


class FakeAlertRepository(_Base):
    async def find(
        self,
        *,
        severity: str | None,
        since: datetime | None,
        acked: bool | None,
        cursor: tuple[datetime, int] | None,
        limit: int,
    ) -> list[Row]:
        rows = [
            a
            for a in self._mine(self.d.alerts)
            if (severity is None or a["severity"] == severity)
            and (since is None or a["ts"] >= since)
            and (acked is None or (a["acked_ts"] is not None) == acked)
            and (cursor is None or (a["ts"], a["id"]) < cursor)
        ]
        return sorted(rows, key=lambda r: (r["ts"], r["id"]), reverse=True)[:limit]

    async def ack(self, alert_id: int, acked_by: str) -> Row | None:
        a = next((a for a in self._mine(self.d.alerts) if a["id"] == alert_id), None)
        if a is None:
            return None
        if a["acked_ts"] is None:
            a["acked_by"], a["acked_ts"] = acked_by, datetime.now(UTC)
        return {"id": a["id"], "acked_by": a["acked_by"], "acked_ts": a["acked_ts"]}

    async def count_open(self) -> int:
        return sum(1 for a in self._mine(self.d.alerts) if a["acked_ts"] is None)


class FakeChargerRepository(_Base):
    async def find(
        self, *, depot_id: int | None, public: bool | None, after_id: int | None, limit: int
    ) -> list[Row]:
        rows = [
            c
            for c in self.d.chargers
            if (c["tenant_id"] == self.t or c["is_public"])
            and (depot_id is None or c["depot_id"] == depot_id)
            and (public is None or c["is_public"] == public)
            and (after_id is None or c["id"] > after_id)
        ]
        return sorted(rows, key=lambda r: r["id"])[:limit]


class FakeTariffRepository(_Base):
    async def current(self) -> Row | None:
        return next((t for t in self._mine(self.d.tariffs)), None)


class FakeReportRepository(_Base):
    async def cost_by_day(self, from_ts: datetime, to_ts: datetime) -> list[Row]:
        days: dict[str, Row] = {}
        for s in self._mine(self.d.sessions):
            if not from_ts <= s["start_ts"] < to_ts:
                continue
            day = days.setdefault(
                s["start_ts"].strftime("%Y-%m-%d"),
                {"kwh": 0.0, "cost": 0.0, "baseline_cost": 0.0, "peak_kw": 0.0},
            )
            hours = (s["end_ts"] - s["start_ts"]).total_seconds() / 3600
            day["kwh"] += s["kwh"]
            day["cost"] += s["cost"] if s["cost"] is not None else s["kwh"] * 12.5
            day["baseline_cost"] += s["kwh"] * 12.5
            day["peak_kw"] = max(day["peak_kw"], s["kwh"] / hours)
        return [{"date": k, **v} for k, v in sorted(days.items())]

    async def battery_health(self, *, after_vin: str | None, limit: int) -> list[Row]:
        mine = self._mine(self.d.battery)
        rows = [b for b in mine if after_vin is None or b["vin"] > after_vin]
        out = []
        for b in sorted(rows, key=lambda r: r["vin"])[:limit]:
            if b.get("embedding") is not None:  # mirrors the pgvector KNN: same tenant, not self, 5 nearest
                others = [o for o in mine if o.get("embedding") is not None and o["vin"] != b["vin"]]
                others.sort(
                    key=lambda o: sum(
                        (x - y) ** 2 for x, y in zip(o["embedding"], b["embedding"], strict=True)
                    )
                )
                b = {**b, "similar": [o["vin"] for o in others[:5]]}
            out.append(b)
        return out


class FakeAgentRepository(_Base):
    async def get(self, action_id: int) -> Row | None:
        return next((a for a in self._mine(self.d.agent_actions) if a["id"] == action_id), None)

    async def decide(self, action_id: int, *, status: str, decided_by: str) -> Row | None:
        a = await self.get(action_id)
        if a is None or a["status"] != "PENDING":
            return None
        a.update(status=status, decided_by=decided_by, decided_ts=datetime.now(UTC))
        return {"id": a["id"], "status": a["status"]}

    async def propose(self, *, action: str, params: dict[str, Any], rationale: str, proposed_by: str) -> Row:
        row = {
            "id": self.d.next_id(),
            "tenant_id": self.t,
            "proposed_by": proposed_by,
            "action": action,
            "params": params,
            "rationale": rationale,
            "status": "PENDING",
            "decided_by": None,
            "decided_ts": None,
            "created_ts": datetime.now(UTC),
        }
        self.d.agent_actions.append(row)
        return {k: row[k] for k in ("id", "action", "params", "rationale", "status", "created_ts")}

    async def set_status(self, action_id: int, *, status: str, from_status: str) -> Row | None:
        a = await self.get(action_id)
        if a is None or a["status"] != from_status:
            return None
        a["status"] = status
        return {"id": a["id"], "status": status}


class FakePrivacyRepository(_Base):
    async def erase_driver(self, driver_id: int) -> bool:
        drv = next((x for x in self._mine(self.d.drivers) if x["id"] == driver_id), None)
        if drv is None:
            return False
        drv.update(name_masked="ERASED", licence_hash="", erased_at=datetime.now(UTC))
        self.d.assignments = [a for a in self.d.assignments if a["driver_id"] != driver_id]
        return True


class FakeAuditRepository(_Base):
    async def insert(
        self, *, actor_id: str, action: str, resource: str, details: dict[str, Any], actor_type: str = "user"
    ) -> int:
        rid = self.d.next_id()
        row = {
            "id": rid,
            "tenant_id": self.t,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "action": action,
            "resource": resource,
            "ts": datetime.now(UTC),
            "details": details,
        }
        self.d.audit.append(row)
        return rid

    async def find(self, *, cursor: tuple[datetime, int] | None, limit: int) -> list[Row]:
        rows = [a for a in self._mine(self.d.audit) if cursor is None or (a["ts"], a["id"]) < cursor]
        return sorted(rows, key=lambda r: (r["ts"], r["id"]), reverse=True)[:limit]


def fake_repos(data: FakeData, tenant_id: int) -> Repos:
    return Repos(
        tenant_id=tenant_id,
        fleets=FakeFleetRepository(data, tenant_id),
        vehicles=FakeVehicleRepository(data, tenant_id),
        plans=FakePlanRepository(data, tenant_id),
        alerts=FakeAlertRepository(data, tenant_id),
        chargers=FakeChargerRepository(data, tenant_id),
        tariffs=FakeTariffRepository(data, tenant_id),
        reports=FakeReportRepository(data, tenant_id),
        agent=FakeAgentRepository(data, tenant_id),
        privacy=FakePrivacyRepository(data, tenant_id),
        audit=FakeAuditRepository(data, tenant_id),
    )


class FakePublisher:
    def __init__(self, fail: bool = False) -> None:
        self.events: list[dict[str, Any]] = []
        self.fail = fail

    async def publish_plan_requested(self, event: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("kafka down")
        self.events.append(event)

    async def ready(self) -> bool:
        return not self.fail
