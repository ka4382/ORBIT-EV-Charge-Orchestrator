"""aiokafka adapters implementing the ProducerPort/BufferGauge ports."""

from __future__ import annotations

import asyncio

from aiokafka import AIOKafkaProducer
from orbit_common.kafka import send_many


class BufferTracker:
    """Counts bytes handed to the producer whose delivery future has not resolved yet."""

    def __init__(self) -> None:
        self._bytes = 0

    @property
    def bytes(self) -> int:
        return self._bytes

    def add(self, n: int) -> None:
        self._bytes += n

    def sub(self, n: int) -> None:
        self._bytes = max(0, self._bytes - n)


class KafkaProducer:
    def __init__(self, producer: AIOKafkaProducer, tracker: BufferTracker | None = None) -> None:
        self._p = producer
        self.tracker = tracker or BufferTracker()

    async def send(self, topic: str, key: bytes | None, value: bytes) -> asyncio.Future:
        fut = await self._p.send(topic, value=value, key=key)
        n = len(value) + (len(key) if key else 0)
        self.tracker.add(n)
        fut.add_done_callback(lambda _f: self.tracker.sub(n))
        return fut

    async def send_many(self, topic: str, records: list[tuple[bytes | None, bytes]]) -> list[asyncio.Future]:
        futures = await send_many(self._p, topic, records)
        n = sum(len(v) + (len(k) if k else 0) for k, v in records)
        self.tracker.add(n)
        if futures:
            done = asyncio.gather(*futures)
            done.add_done_callback(lambda _f: self.tracker.sub(n))
        return futures

    async def flush(self) -> None:
        await self._p.flush()
