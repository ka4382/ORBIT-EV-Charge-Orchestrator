from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import orjson
from aiokafka.structs import TopicPartition

VIN = "1HGCM82633A004352"
NOW = datetime(2026, 9, 25, 10, 15, 2, 120000, tzinfo=UTC)


def oem_a(seq: int = 1, **over: Any) -> dict[str, Any]:
    d = {
        "oem": "OEM_A",
        "vin": VIN,
        "ts": NOW.isoformat().replace("+00:00", "Z"),
        "seq": seq,
        "tenant": 12,
        "lat": 21.1702,
        "lon": 72.8311,
        "speed_kmh": 64.2,
        "soc": 41.0,
        "soh": 93.5,
        "odo_km": 18234.7,
        "amb_c": 31.2,
        "dtc": ["P0301"],
        "evt": "HARSH_BRAKE",
        "chg": {"st": "NONE", "kw": 0},
    }
    d.update(over)
    return d


def oem_b(seq: int = 1, **over: Any) -> dict[str, Any]:
    d = {
        "oem": "OEM_B",
        "vehicle": {"vin": VIN, "tenant_id": 12},
        "t": int(NOW.timestamp() * 1000),
        "n": seq,
        "pos": {"latitude": 21.1702, "longitude": 72.8311},
        "velocity_mph": 39.9,
        "battery": {"level": 0.41, "health": 0.935, "charge_mode": 2, "charge_kw": 48.5},
        "odometer_mi": 11330.5,
        "temp_f": 88.2,
        "faults": "P0301;P0A80",
        "event_code": "HB",
    }
    d.update(over)
    return d


@dataclass
class FakeRecord:
    value: bytes | None
    offset: int = 0
    key: bytes | None = None
    topic: str = "telemetry.raw"
    partition: int = 0


def rec(payload: dict[str, Any] | bytes, offset: int = 0) -> FakeRecord:
    return FakeRecord(value=payload if isinstance(payload, bytes) else orjson.dumps(payload), offset=offset)


class FakeProducer:
    def __init__(self, fail_topics: set[str] | None = None) -> None:
        self.sent: dict[str, list[tuple[bytes | None, bytes]]] = {}
        self.flushed = 0
        self.fail_topics = fail_topics or set()

    async def send(self, topic: str, key: bytes | None, value: bytes) -> asyncio.Future:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        if topic in self.fail_topics:
            fut.set_exception(RuntimeError(f"send to {topic} failed"))
        else:
            self.sent.setdefault(topic, []).append((key, value))
            fut.set_result(None)
        return fut

    async def send_many(self, topic: str, records: list[tuple[bytes | None, bytes]]) -> list[asyncio.Future]:
        return [await self.send(topic, k, v) for k, v in records]

    async def flush(self) -> None:
        self.flushed += 1


class FakeConsumer:
    """Feeds pre-canned batches to the runner; records commits/pauses."""

    def __init__(self, batches: list[dict[TopicPartition, list[FakeRecord]]]) -> None:
        self._batches = list(batches)
        self.commits: list[dict[TopicPartition, int]] = []
        self.paused_parts: set[TopicPartition] = set()
        self.pause_calls = 0
        self.resume_calls = 0
        self.polls = 0

    async def getmany(self, *, timeout_ms: int, max_records: int) -> dict[TopicPartition, list[FakeRecord]]:
        self.polls += 1
        await asyncio.sleep(0)
        if self._batches and not self.paused_parts:
            return self._batches.pop(0)
        return {}

    async def commit(self, offsets: dict[TopicPartition, int]) -> None:
        self.commits.append(dict(offsets))

    def pause(self, *partitions: TopicPartition) -> None:
        self.pause_calls += 1
        self.paused_parts.update(partitions)

    def resume(self, *partitions: TopicPartition) -> None:
        self.resume_calls += 1
        self.paused_parts.difference_update(partitions)

    def assignment(self) -> set[TopicPartition]:
        return {TopicPartition("telemetry.raw", 0)}

    def paused(self) -> set[TopicPartition]:
        return set(self.paused_parts)


@dataclass
class SetDeduper:
    keys: set[bytes] = field(default_factory=set)

    def seen(self, key: bytes) -> bool:
        if key in self.keys:
            return True
        self.keys.add(key)
        return False


@dataclass
class FakeBuffer:
    bytes: int = 0
