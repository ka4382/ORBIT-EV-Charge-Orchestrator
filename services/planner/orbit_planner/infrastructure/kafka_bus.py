"""EventPublisher: `plans.ready` on Kafka (key = vehicle_id, contracts/topics.yaml) + Redis pub/sub."""

from __future__ import annotations

import orjson
from aiokafka import AIOKafkaProducer
from orbit_common import kafka as kafka_common
from orbit_common.metrics import EVENTS_OUT

from .redis_store import RedisStateStore

TOPIC_READY = "plans.ready"
TOPIC_REQUESTED = "plans.requested"


class KafkaRedisPublisher:
    def __init__(self, redis_store: RedisStateStore, producer: AIOKafkaProducer | None = None) -> None:
        self.redis = redis_store
        self.producer = producer or kafka_common.producer()

    async def start(self) -> None:
        await self.producer.start()

    async def stop(self) -> None:
        await self.producer.stop()

    async def plan_ready(self, tenant_id: int, vehicle_id: int | None, event: dict[str, object]) -> None:
        key = str(vehicle_id).encode() if vehicle_id is not None else None
        await self.producer.send_and_wait(TOPIC_READY, orjson.dumps(event), key=key)
        EVENTS_OUT.labels("planner", TOPIC_READY).inc()
        await self.redis.publish(tenant_id, event)
