"""Ports (interfaces) the ingest use cases depend on. Implemented by infrastructure/ and by test fakes."""

from __future__ import annotations

from collections.abc import Awaitable, Hashable, Iterable, Sequence
from typing import Any, Protocol


class Record(Protocol):
    """Shape of a consumed record (aiokafka ConsumerRecord satisfies this structurally)."""

    @property
    def key(self) -> bytes | None: ...
    @property
    def value(self) -> bytes | None: ...
    @property
    def offset(self) -> int: ...


class ProducerPort(Protocol):
    async def send(self, topic: str, key: bytes | None, value: bytes) -> Awaitable[Any]:
        """Enqueue a record. Returns an awaitable that resolves once the broker acknowledged it."""
        ...

    async def send_many(
        self, topic: str, records: list[tuple[bytes | None, bytes]]
    ) -> Sequence[Awaitable[Any]]:
        """Enqueue many records at once (hot path). Returns awaitables resolving on broker ack."""
        ...

    async def flush(self) -> None: ...


class ConsumerPort(Protocol):
    async def getmany(self, *, timeout_ms: int, max_records: int) -> dict[Any, list[Any]]: ...
    async def commit(self, offsets: dict[Any, int]) -> None: ...
    def pause(self, *partitions: Hashable) -> None: ...
    def resume(self, *partitions: Hashable) -> None: ...
    def assignment(self) -> set[Any]: ...
    def paused(self) -> Iterable[Any]: ...


class BufferGauge(Protocol):
    """Bytes handed to the producer but not yet acknowledged (drives back-pressure)."""

    @property
    def bytes(self) -> int: ...
