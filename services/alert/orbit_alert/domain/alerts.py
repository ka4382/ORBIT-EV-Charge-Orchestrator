"""Alert value object (mirrors the `alert` table and the WS/Kafka `alert` payload)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import orjson

LOW_SOC = "LOW_SOC"
STRAND_RISK = "STRAND_RISK"
SOC_DROP = "SOC_DROP"
SOH_DROP = "SOH_DROP"
BATTERY_DTC = "BATTERY_DTC"
CHARGE_INTERRUPTED = "CHARGE_INTERRUPTED"

INFO, WARN, CRITICAL = "INFO", "WARN", "CRITICAL"


@dataclass(slots=True)
class Alert:
    tenant_id: int
    vin: str
    type: str
    severity: str
    ts: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    vehicle_id: int | None = None

    @property
    def kafka_key(self) -> bytes:
        return f"{self.tenant_id}:{self.vin}".encode()

    @property
    def dedupe_key(self) -> str:
        return f"alert:dedupe:{self.vin}:{self.type}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "vin": self.vin,
            "tenant_id": self.tenant_id,
            "type": self.type,
            "severity": self.severity,
            "ts": self.ts.isoformat().replace("+00:00", "Z"),
            "payload": self.payload,
            "acked_by": None,
            "acked_ts": None,
        }

    def to_json(self) -> bytes:
        return orjson.dumps(self.to_dict())

    def ws_message(self) -> bytes:
        return orjson.dumps({"type": "alert", "data": self.to_dict()})
