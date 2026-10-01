"""Outbound port for `plans.requested` (contracts/topics.yaml, key = vehicle_id)."""

from __future__ import annotations

from typing import Any, Protocol

import orjson
import structlog
from orbit_common.kafka import producer as make_producer
from orbit_common.metrics import EVENTS_OUT

log = structlog.get_logger("orbit_api.publisher")
TOPIC_PLANS_REQUESTED = "plans.requested"


class PlanPublisher(Protocol):
    async def publish_plan_requested(self, event: dict[str, Any]) -> None: ...
    async def ready(self) -> bool: ...


class KafkaPlanPublisher:
    def __init__(self) -> None:
        self._producer = make_producer()
        self._started = False

    async def start(self) -> None:
        await self._producer.start()
        self._started = True

    async def stop(self) -> None:
        if self._started:
            await self._producer.stop()
            self._started = False

    async def publish_plan_requested(self, event: dict[str, Any]) -> None:
        if not self._started:
            raise RuntimeError("kafka producer not started")
        await self._producer.send_and_wait(
            TOPIC_PLANS_REQUESTED, orjson.dumps(event), key=str(event["vehicle_id"]).encode()
        )
        EVENTS_OUT.labels(service="api", topic=TOPIC_PLANS_REQUESTED).inc()

    async def ready(self) -> bool:
        if not self._started:
            return False
        try:
            await self._producer.client.fetch_all_metadata()
            return True
        except Exception:
            return False


class NullPlanPublisher:
    """Used when Kafka is unreachable at startup: POST /plans answers 503 instead of crashing the API."""

    async def publish_plan_requested(self, event: dict[str, Any]) -> None:
        raise RuntimeError("kafka unavailable")

    async def ready(self) -> bool:
        return False
