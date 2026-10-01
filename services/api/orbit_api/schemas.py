"""Pydantic v2 response/request models — the shapes in contracts/api.md, verbatim."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field

T = TypeVar("T")


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


class Items[T](BaseModel):
    items: list[T]


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str
    trace_id: str


PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": Problem, "content": {"application/problem+json": {}}},
    403: {"model": Problem, "content": {"application/problem+json": {}}},
    404: {"model": Problem, "content": {"application/problem+json": {}}},
    429: {"model": Problem, "content": {"application/problem+json": {}}},
}


class VehicleState(BaseModel):
    model_config = ConfigDict(extra="allow")
    vin: str
    tenant_id: int | None = None
    ts: str | None = None
    seq: int | None = None
    lat: float | None = None
    lon: float | None = None
    speed_kmh: float | None = None
    soc_pct: float | None = None
    soh_pct: float | None = None
    odo_km: float | None = None
    charging_state: str = "NONE"
    charging_kw: float = 0.0
    dtc: list[str] = Field(default_factory=list)
    status: str = "OFFLINE"


class Fleet(BaseModel):
    id: int
    name: str
    depot_id: int | None = None
    vehicle_count: int = 0


class FleetSummary(BaseModel):
    vehicles: int = 0
    online: int = 0
    charging: int = 0
    at_risk: int = 0
    avg_soc: float = 0.0
    open_alerts: int = 0
    kwh_today: float = 0.0
    cost_today: float = 0.0
    savings_today: float = 0.0


class VehicleItem(BaseModel):
    id: int
    vin: str
    plate: str | None = None
    fleet_id: int
    model: str
    powertrain: str
    state: VehicleState | None = None


class VehicleDetail(VehicleItem):
    tenant_id: int
    oem: str | None = None
    battery_capacity_kwh: float | None = None
    nominal_range_km: float | None = None
    created_at: datetime | None = None


class TelemetryRow(BaseModel):
    ts: datetime
    vin: str
    seq: int
    lat: float | None = None
    lon: float | None = None
    speed_kmh: float | None = None
    soc_pct: float | None = None
    soh_pct: float | None = None
    odo_km: float | None = None
    ambient_c: float | None = None
    charging_state: int = 0
    charging_kw: float = 0.0
    dtc: list[str] = Field(default_factory=list)
    evt: str | None = None


class NearestCharger(BaseModel):
    charger_id: int
    name: str
    lat: float
    lon: float
    max_kw: float
    distance_km: float
    reachable: bool
    price_per_kwh: float | None = None


class RangeForecast(BaseModel):
    range_km: float
    method: str
    confidence: float


class PlanCreate(BaseModel):
    vin: str = Field(min_length=17, max_length=17, pattern=r"^[A-HJ-NPR-Z0-9]{17}$")
    target_soc: float = Field(gt=0, le=100)
    depart_ts: datetime


class PlanAccepted(BaseModel):
    plan_id: int
    status: str = "PENDING"


class PlanSlot(BaseModel):
    charger_id: int
    start_ts: datetime
    end_ts: datetime
    kw: float
    kwh: float
    cost: float
    price_per_kwh: float | None = None


class PlanItem(BaseModel):
    id: int
    vin: str
    status: str
    target_soc: float
    depart_ts: datetime
    total_cost: float | None = None
    baseline_cost: float | None = None
    savings: float | None = None
    achievable_soc: float | None = None  # SoC the schedule reaches by departure

    @computed_field  # type: ignore[prop-decorator]
    @property
    def infeasible(self) -> bool:
        """True when the planned schedule cannot reach the requested target before departure."""
        return self.achievable_soc is not None and self.achievable_soc + 0.5 < self.target_soc


class PlanDetail(PlanItem):
    slots: list[PlanSlot] = Field(default_factory=list)


class Alert(BaseModel):
    id: int
    vin: str
    type: str
    severity: str
    ts: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    acked_by: str | None = None
    acked_ts: datetime | None = None


class AlertAck(BaseModel):
    id: int
    acked_by: str
    acked_ts: datetime


class Charger(BaseModel):
    id: int
    tenant_id: int | None = None
    depot_id: int | None = None
    name: str
    lat: float
    lon: float
    max_kw: float
    connector: str
    is_public: bool
    price_per_kwh: float | None = None


class TariffSlot(BaseModel):
    dow: int
    start_min: int
    end_min: int
    price_per_kwh: float


class Tariff(BaseModel):
    tariff_id: int
    name: str
    slots: list[TariffSlot]


class CostDay(BaseModel):
    date: str
    kwh: float
    cost: float
    baseline_cost: float
    savings: float
    peak_kw: float


class CostReport(BaseModel):
    days: list[CostDay]


class BatteryHealth(BaseModel):
    vin: str
    soh_pct: float
    cycles: int | None = None
    trend_30d: float | None = None
    similar: list[str] = Field(default_factory=list)


class AgentQuery(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AgentAction(BaseModel):
    id: int
    action: str
    params: dict[str, Any]
    rationale: str
    status: str


class AgentAnswer(BaseModel):
    answer: str
    actions: list[AgentAction]
    audit_id: int | None = None


class AgentDecision(BaseModel):
    id: int
    status: str


class ErasureAccepted(BaseModel):
    job_id: str


class AuditRow(BaseModel):
    id: int
    tenant_id: int | None = None
    actor_type: str
    actor_id: str
    action: str
    resource: str
    ts: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class Health(BaseModel):
    status: str = "ok"


class Readiness(BaseModel):
    kafka: bool
    postgres: bool
    redis: bool
