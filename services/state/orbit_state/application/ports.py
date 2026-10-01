"""Ports the state use cases depend on (implemented by infrastructure/ and by test fakes)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from orbit_state.domain.state import LastSeen


@dataclass(slots=True)
class StateWrite:
    vin: str
    tenant_id: int
    lat: float
    lon: float
    value: bytes  # VehicleState JSON stored at veh:{vin} (one SET ... EX per vehicle)
    publish: bytes | None = None  # `{"type":"state",...}` when not throttled


@dataclass(slots=True)
class SummaryWrite:
    tenant_id: int
    fields: dict[str, str]
    publish: bytes


@dataclass(slots=True)
class WriteBatch:
    states: list[StateWrite] = field(default_factory=list)
    summaries: list[SummaryWrite] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.states) + len(self.summaries)


class StateStore(Protocol):
    async def fetch_last_seen(self, vins: Iterable[str]) -> dict[str, LastSeen]: ...
    async def write(self, batch: WriteBatch) -> None: ...


class ConsumerPort(Protocol):
    async def getmany(self, *, timeout_ms: int, max_records: int) -> dict[Any, list[Any]]: ...
    async def commit(self, offsets: dict[Any, int]) -> None: ...
