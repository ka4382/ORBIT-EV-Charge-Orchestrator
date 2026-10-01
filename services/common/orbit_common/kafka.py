"""aiokafka helpers: topic bootstrap from contracts/topics.yaml, producer/consumer factories."""

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
from typing import Any

import yaml
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.admin.config_resource import ConfigResource, ConfigResourceType
from aiokafka.errors import (
    CommitFailedError,
    IllegalStateError,
    RebalanceInProgressError,
    TopicAlreadyExistsError,
)

from .config import settings

_CONTRACT = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "topics.yaml"


def topic_contract() -> dict[str, dict[str, Any]]:
    for candidate in (
        _CONTRACT,
        pathlib.Path("/app/contracts/topics.yaml"),
        pathlib.Path("contracts/topics.yaml"),
    ):
        if candidate.exists():
            return yaml.safe_load(candidate.read_text())["topics"]
    raise FileNotFoundError("contracts/topics.yaml not found")


def topic_configs(spec: dict[str, Any]) -> dict[str, str]:
    """Per-topic config from the contract, capped by the environment.

    `contracts/topics.yaml` holds production retention (raw 24 h, canonical 7 d). On a laptop that is
    hundreds of GB at 20K events/s, so `KAFKA_RETENTION_MS_MAX` and `KAFKA_RETENTION_BYTES_PER_PARTITION`
    cap it (.env sets both); unset means the contract applies unchanged.
    """
    retention = int(spec["retention_ms"])
    cap = os.environ.get("KAFKA_RETENTION_MS_MAX")
    if cap:
        retention = min(retention, int(cap))
    cfg = {"retention.ms": str(retention)}
    size_cap = os.environ.get("KAFKA_RETENTION_BYTES_PER_PARTITION")
    if size_cap:
        cfg["retention.bytes"] = str(int(size_cap))
    return cfg


async def ensure_topics(partitions_override: int | None = None) -> None:
    """Create missing topics and re-apply retention to existing ones (idempotent, safe on every start)."""
    admin = AIOKafkaAdminClient(bootstrap_servers=settings().kafka_bootstrap)
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        contract = topic_contract()
        new = [
            NewTopic(
                name,
                num_partitions=partitions_override or spec["partitions"],
                replication_factor=1,
                topic_configs=topic_configs(spec),
            )
            for name, spec in contract.items()
            if name not in existing
        ]
        if new:
            with contextlib.suppress(TopicAlreadyExistsError):
                await admin.create_topics(new)
        resources = [
            ConfigResource(ConfigResourceType.TOPIC, name, configs=topic_configs(spec))
            for name, spec in contract.items()
            if name in existing
        ]
        if resources:
            await admin.alter_configs(resources)
    finally:
        await admin.close()


async def wait_for_kafka(timeout_s: float = 120) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        try:
            await ensure_topics()
            return
        except Exception:
            if asyncio.get_event_loop().time() > deadline:
                raise
            await asyncio.sleep(2)


def producer(**kw: Any) -> AIOKafkaProducer:
    """Idempotent, acks=all, lz4 producer. Caller keyword arguments override these defaults."""
    opts: dict[str, Any] = {
        "bootstrap_servers": settings().kafka_bootstrap,
        "acks": "all",
        "enable_idempotence": True,
        "linger_ms": 20,
        "max_batch_size": 256 * 1024,
        "compression_type": "lz4",
    }
    opts.update(kw)
    return AIOKafkaProducer(**opts)


def consumer(*topics: str, group: str, **kw: Any) -> AIOKafkaConsumer:
    """Manual-commit consumer tuned for batch reads. Caller keyword arguments override these defaults."""
    opts: dict[str, Any] = {
        "bootstrap_servers": settings().kafka_bootstrap,
        "group_id": group,
        "enable_auto_commit": False,
        "auto_offset_reset": "earliest",
        "max_poll_records": 2000,
        "fetch_max_bytes": 64 * 1024 * 1024,
    }
    opts.update(kw)
    return AIOKafkaConsumer(*topics, **opts)


# (topic, partition count) -> {key: partition}. Invalidated naturally when the partition count changes.
_PARTITION_CACHE: dict[tuple[str, int], dict[bytes, int]] = {}


async def send_many(
    p: AIOKafkaProducer, topic: str, records: list[tuple[bytes | None, bytes]]
) -> list[asyncio.Future[Any]]:
    """Send many records with one `send_batch` per partition instead of one `send` per record.

    Per-record `send` costs ~9 us of Python per event; building batches directly costs ~0.6 us. Records
    are partitioned with the producer's own partitioner (murmur2 on the key), so per-VIN ordering and
    placement are identical to `send`. Returns one delivery future per batch.
    """
    if not records:
        return []
    await p.client._wait_on_metadata(topic)
    n_parts = len(p.client.cluster.partitions_for_topic(topic) or ())
    cache = _PARTITION_CACHE.setdefault((topic, n_parts), {})
    by_partition: dict[int, list[tuple[bytes | None, bytes]]] = {}
    for key, value in records:
        part = cache.get(key) if key is not None else None
        if part is None:
            part = p._partition(topic, None, key, value, key, value)
            if key is not None and len(cache) < 500_000:
                cache[key] = part  # murmur2 is pure Python in aiokafka; a VIN's partition never changes
        by_partition.setdefault(part, []).append((key, value))
    futures: list[asyncio.Future[Any]] = []
    for part, items in by_partition.items():
        batch = p.create_batch()
        for key, value in items:
            if batch.append(key=key, value=value, timestamp=None) is None:
                batch.close()
                futures.append(await p.send_batch(batch, topic, partition=part))
                batch = p.create_batch()
                batch.append(key=key, value=value, timestamp=None)
        batch.close()
        futures.append(await p.send_batch(batch, topic, partition=part))
    return futures


async def safe_commit(consumer: Any, offsets: dict[Any, int] | None = None) -> int:
    """Commit only offsets for partitions this member still owns; tolerate a concurrent rebalance.

    After a rebalance a member may hold processed-but-uncommitted offsets for partitions that moved to
    another member. Committing them raises (IllegalStateError / CommitFailedError) and used to crash the
    service, which triggered another rebalance: a crash loop whenever replicas were added. Dropping those
    offsets is safe because delivery is at-least-once and every consumer is idempotent (ADR-003): the new
    owner re-reads from the last committed offset. Returns how many partition offsets were committed.
    """
    if offsets is None:
        try:
            await consumer.commit()
        except (CommitFailedError, IllegalStateError, RebalanceInProgressError):
            return 0
        return 1
    owned = consumer.assignment()
    mine = {tp: off for tp, off in offsets.items() if tp in owned}
    if not mine:
        return 0
    try:
        await consumer.commit(mine)
    except (CommitFailedError, IllegalStateError, RebalanceInProgressError):
        return 0
    return len(mine)
