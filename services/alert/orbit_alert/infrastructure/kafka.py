"""aiokafka producer adapter for the `alerts` topic (AlertPublisher port)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from aiokafka import AIOKafkaProducer

from orbit_alert.domain.alerts import Alert


class KafkaAlertPublisher:
    def __init__(self, producer: AIOKafkaProducer, topic: str = "alerts") -> None:
        self._p = producer
        self._topic = topic

    async def emit(self, alerts: Sequence[Alert]) -> None:
        futs = [await self._p.send(self._topic, value=a.to_json(), key=a.kafka_key) for a in alerts]
        if futs:
            await asyncio.gather(*futs)
