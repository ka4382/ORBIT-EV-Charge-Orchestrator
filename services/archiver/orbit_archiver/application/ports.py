"""Ports the archiver depends on (implemented by infrastructure/ and by test fakes)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class TelemetryRepository(Protocol):
    async def copy(self, rows: Sequence[str]) -> int:
        """Bulk-load COPY text rows; must be idempotent on (vin, seq, ts). Returns rows attempted."""
        ...


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...


class ConsumerPort(Protocol):
    async def getmany(self, *, timeout_ms: int, max_records: int) -> dict[Any, list[Any]]: ...
    async def commit(self, offsets: dict[Any, int]) -> None: ...
