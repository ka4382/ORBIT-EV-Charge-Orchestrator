"""End-to-end: raw OEM events -> ingest -> canonical/DLQ -> state (Redis) -> archiver (Timescale) -> alert."""

# ruff: noqa: ASYNC110 - polling on real brokers with short sleeps is intended here
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
import psycopg
import pytest
import redis.asyncio as aioredis
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from orbit_alert.application.engine import AlertEngine, VehicleCache
from orbit_alert.domain.alerts import Alert
from orbit_alert.infrastructure.postgres import PostgresAlertRepo
from orbit_alert.infrastructure.redis_store import GEO_CHARGERS, RedisAlertStore
from orbit_archiver.application.archiver import ArchiverService
from orbit_archiver.application.runner import consume as archiver_consume
from orbit_archiver.infrastructure.postgres import PostgresTelemetryRepo
from orbit_common import kafka as kafka_helpers
from orbit_common.events import CanonicalEvent
from orbit_common.streaming import RollingBloom
from orbit_common.vin import make_valid
from orbit_ingest.application.pipeline import IngestPipeline
from orbit_ingest.application.runner import IngestRunner
from orbit_ingest.domain.processing import DtcTracker
from orbit_ingest.infrastructure.kafka import BufferTracker, KafkaProducer
from orbit_state.application.runner import consume as state_consume
from orbit_state.application.service import StateService
from orbit_state.infrastructure.redis_store import RedisStateStore

pytestmark = pytest.mark.integration

VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
N_VEHICLES, EVENTS_PER_VEHICLE, DUPLICATES, MALFORMED = 200, 5, 50, 5
TENANTS = (1, 2)
IT_TIMEOUT_S = 90


@dataclass
class Ingested:
    vins: list[str]
    unique: int
    raw_records: int
    duplicates: int
    malformed: int
    canonical: int
    dlq: int
    runner_totals: dict[str, Any]


def _vin(rng: random.Random) -> str:
    return make_valid("".join(rng.choice(VIN_CHARS) for _ in range(17)))


def _raw_events(vins: list[str], rng: random.Random) -> list[bytes]:
    now = datetime.now(UTC)
    out: list[bytes] = []
    for i, vin in enumerate(vins):
        tenant = TENANTS[i % len(TENANTS)]
        for seq in range(1, EVENTS_PER_VEHICLE + 1):
            ts = now - timedelta(seconds=EVENTS_PER_VEHICLE - seq)
            soc = 60.0 - seq if i % 10 else 12.0  # every 10th vehicle is low on charge
            if i % 2 == 0:
                out.append(
                    orjson.dumps(
                        {
                            "oem": "OEM_A",
                            "vin": vin,
                            "ts": ts.isoformat().replace("+00:00", "Z"),
                            "seq": seq,
                            "tenant": tenant,
                            "lat": 13.08 + i * 1e-4,
                            "lon": 80.27,
                            "speed_kmh": 30.0,
                            "soc": soc,
                            "soh": 92.0,
                            "odo_km": 1000.0 + seq,
                            "amb_c": 30.0,
                            "dtc": ["P0301"] if seq == 3 else [],
                            "evt": None,
                            "chg": {"st": "NONE", "kw": 0},
                        }
                    )
                )
            else:
                out.append(
                    orjson.dumps(
                        {
                            "oem": "OEM_B",
                            "vehicle": {"vin": vin, "tenant_id": tenant},
                            "t": int(ts.timestamp() * 1000),
                            "n": seq,
                            "pos": {"latitude": 13.08 + i * 1e-4, "longitude": 80.27},
                            "velocity_mph": 18.6,
                            "battery": {
                                "level": soc / 100,
                                "health": 0.92,
                                "charge_mode": 1,
                                "charge_kw": 7.2,
                            },
                            "odometer_mi": 621.0 + seq,
                            "temp_f": 86.0,
                            "faults": "",
                            "event_code": "",
                        }
                    )
                )
    return out


async def _drain_topic(bootstrap: str, topic: str, quiet_s: float = 3.0) -> list[bytes]:
    c = AIOKafkaConsumer(
        topic, bootstrap_servers=bootstrap, auto_offset_reset="earliest", enable_auto_commit=False
    )
    await c.start()
    try:
        out: list[bytes] = []
        last = time.monotonic()
        while time.monotonic() - last < quiet_s:
            polled = await c.getmany(timeout_ms=200, max_records=5000)
            for recs in polled.values():
                out.extend(r.value for r in recs)
                last = time.monotonic()
        return out
    finally:
        await c.stop()


async def _run_ingest(bootstrap: str) -> Ingested:
    rng = random.Random(7)
    vins = [_vin(rng) for _ in range(N_VEHICLES)]
    raws = _raw_events(vins, rng)
    unique = len(raws)
    raws += rng.sample(raws, DUPLICATES)
    raws += [
        b"{not json",
        b"[1,2]",
        orjson.dumps({"oem": "OEM_Z", "vin": vins[0]}),
        orjson.dumps({"oem": "OEM_A", "vin": vins[0]}),
        b"",
    ][:MALFORMED]
    rng.shuffle(raws)

    await kafka_helpers.wait_for_kafka(timeout_s=60)
    p = AIOKafkaProducer(bootstrap_servers=bootstrap, linger_ms=5)
    await p.start()
    try:
        await asyncio.gather(*[await p.send("telemetry.raw", value=r) for r in raws])
    finally:
        await p.stop()

    raw_producer = kafka_helpers.producer()
    consumer = kafka_helpers.consumer("telemetry.raw", group="ingest-it")
    await raw_producer.start()
    await consumer.start()
    tracker = BufferTracker()
    pipeline = IngestPipeline(KafkaProducer(raw_producer, tracker), RollingBloom(2_000_000), DtcTracker())
    runner = IngestRunner(consumer, KafkaProducer(raw_producer, tracker), pipeline, tracker, max_in_flight=4)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop))
    deadline = time.monotonic() + IT_TIMEOUT_S
    try:
        while runner.totals.consumed < len(raws) and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
    finally:
        stop.set()
        await task
        await raw_producer.stop()
        await consumer.stop()
    canonical = await _drain_topic(bootstrap, "telemetry.canonical")
    dlq = await _drain_topic(bootstrap, "telemetry.dlq")
    for raw in dlq:
        env = orjson.loads(raw)
        assert {"reason", "raw"} <= set(env)
    return Ingested(
        vins,
        unique,
        len(raws),
        DUPLICATES,
        MALFORMED,
        len(canonical),
        len(dlq),
        {
            "consumed": runner.totals.consumed,
            "produced": runner.totals.produced,
            "duplicates": runner.totals.duplicates,
            "dlq": runner.totals.dlq,
            "reasons": runner.totals.reasons,
        },
    )


@pytest.fixture(scope="session")
def ingested(orbit_env: dict[str, str]) -> Ingested:
    return asyncio.run(_run_ingest(orbit_env["kafka"]))


def test_ingest_dedupes_and_routes_dlq(ingested: Ingested) -> None:
    assert ingested.raw_records == ingested.unique + ingested.duplicates + ingested.malformed
    assert ingested.runner_totals["consumed"] == ingested.raw_records
    assert ingested.canonical == ingested.unique, ingested.runner_totals
    assert ingested.runner_totals["duplicates"] == ingested.duplicates
    assert ingested.dlq == ingested.malformed
    assert ingested.runner_totals["reasons"].keys() >= {
        "duplicate",
        "malformed_json",
        "unknown_oem",
        "invalid",
    }


async def test_state_writes_redis_hashes(orbit_env: dict[str, str], ingested: Ingested) -> None:
    store = RedisStateStore.from_url(orbit_env["redis"])
    service = StateService(store)
    consumer = kafka_helpers.consumer("telemetry.canonical", group="state-it")
    await consumer.start()
    stop = asyncio.Event()
    task = asyncio.create_task(state_consume(consumer, service, stop))
    deadline = time.monotonic() + IT_TIMEOUT_S
    try:
        while (
            sum(len(t) for t in [service.tracker.tenants()]) < len(TENANTS) or _tracked(service) < N_VEHICLES
        ):
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(0.2)
        await service.publish_summaries()
    finally:
        stop.set()
        await task
        await consumer.stop()
    r = aioredis.Redis.from_url(orbit_env["redis"], decode_responses=True)
    try:
        vin = ingested.vins[0]
        h = orjson.loads(await r.get(f"veh:{vin}"))
        assert (
            h["vin"] == vin
            and h["seq"] == EVENTS_PER_VEHICLE
            and h["status"] in {"DRIVING", "CHARGING", "AT_RISK"}
        )
        assert h["dtc"] == [] and 0 < int(await r.ttl(f"veh:{vin}")) <= 600
        total = sum([await r.scard(f"tenant:{t}:vins") for t in TENANTS])
        assert total == N_VEHICLES
        assert await r.zcard(f"geo:vehicles:{TENANTS[0]}") == N_VEHICLES // 2
        summary = await r.hgetall(f"summary:{TENANTS[0]}")
        assert int(summary["vehicles"]) == N_VEHICLES // 2 and int(summary["online"]) == N_VEHICLES // 2
        assert int(summary["at_risk"]) > 0 and float(summary["avg_soc"]) > 0
    finally:
        await r.aclose()
        await store.close()


def _tracked(service: StateService) -> int:
    return sum(service.tracker.summary(t)["vehicles"] for t in service.tracker.tenants())


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data


async def test_archiver_copies_into_timescale(orbit_env: dict[str, str], ingested: Ingested) -> None:
    repo = PostgresTelemetryRepo.from_url(orbit_env["postgres"], min_size=1, max_size=2)
    await repo.open()
    store = MemoryStore()
    service = ArchiverService(repo, store, pg_flush_rows=500, pg_flush_interval_s=0.5, parquet_interval_s=600)
    consumer = kafka_helpers.consumer("telemetry.canonical", group="archiver-it")
    await consumer.start()
    stop = asyncio.Event()
    task = asyncio.create_task(archiver_consume(consumer, service, stop))
    deadline = time.monotonic() + IT_TIMEOUT_S
    try:
        while service.stats.pg_rows < ingested.unique and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
    finally:
        stop.set()
        await task  # flushes Postgres + Parquet on shutdown
        await consumer.stop()
    assert service.stats.pg_rows == ingested.unique and repo.fallbacks == 0
    assert store.objects and sum(1 for k in store.objects if "/tenant_id=" in k) == len(store.objects)
    with psycopg.connect(orbit_env["postgres"]) as conn:
        (n,) = conn.execute("SELECT count(*) FROM telemetry").fetchone()
        (n_dtc,) = conn.execute("SELECT count(*) FROM telemetry WHERE 'P0301' = ANY(dtc)").fetchone()
        (n_ac,) = conn.execute("SELECT count(*) FROM telemetry WHERE charging_state = 1").fetchone()
    assert n == ingested.unique
    assert n_dtc == N_VEHICLES // 2  # one OEM_A event per vehicle carried the DTC
    assert n_ac == (N_VEHICLES // 2) * EVENTS_PER_VEHICLE  # OEM_B vehicles were AC charging
    # replaying the same rows must not raise or duplicate: the COPY falls back to the staging merge
    canonical = await _drain_topic(orbit_env["kafka"], "telemetry.canonical")
    from orbit_archiver.domain.rows import copy_row

    rows = [copy_row(CanonicalEvent.from_json(v)) for v in canonical[:100]]
    assert await repo.copy(rows) == 100 and repo.fallbacks == 1
    with psycopg.connect(orbit_env["postgres"]) as conn:
        (n2,) = conn.execute("SELECT count(*) FROM telemetry").fetchone()
    assert n2 == n
    await repo.close()


class CapturePublisher:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    async def emit(self, alerts: list[Alert]) -> None:
        self.alerts.extend(alerts)


def _seed_vehicle(url: str, vin: str, lat: float, lon: float) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("INSERT INTO tenant(id, name) VALUES (1, 'IT tenant') ON CONFLICT DO NOTHING")
        conn.execute(
            "INSERT INTO oem(id, code, name, payload_format) VALUES (1, 'OEM_A', 'A', 'flat') "
            "ON CONFLICT DO NOTHING"
        )
        conn.execute(
            "INSERT INTO vehicle_model(id, oem_id, name, powertrain, battery_capacity_kwh, nominal_range_km) "
            "VALUES (1, 1, 'A-Van 60', 'BEV', 60, 300) ON CONFLICT DO NOTHING"
        )
        conn.execute("INSERT INTO fleet(id, tenant_id, name) VALUES (1, 1, 'F1') ON CONFLICT DO NOTHING")
        conn.execute(
            "INSERT INTO vehicle(vin, tenant_id, fleet_id, model_id) VALUES (%s, 1, 1, 1) "
            "ON CONFLICT DO NOTHING",
            (vin,),
        )
        conn.execute(
            "INSERT INTO charger(tenant_id, name, lat, lon, max_kw, connector, is_public) "
            "VALUES (NULL, 'far away', %s, %s, 50, 'CCS2', true)",
            (lat + 5.0, lon + 5.0),  # ~700 km away: unreachable at 10 % SoC
        )


async def test_alert_engine_against_real_redis_and_postgres(orbit_env: dict[str, str]) -> None:
    vin = make_valid("1HGCM82633A004352")
    _seed_vehicle(orbit_env["postgres"], vin, 13.08, 80.27)
    redis_store = RedisAlertStore.from_url(orbit_env["redis"])
    repo = PostgresAlertRepo.from_url(orbit_env["postgres"])
    await repo.open()
    try:
        assert not await redis_store.chargers_loaded()
        assert await redis_store.load_chargers(await repo.chargers()) >= 1
        assert await redis_store.chargers_loaded()
        publisher = CapturePublisher()
        engine = AlertEngine(VehicleCache(repo), redis_store, redis_store, repo, publisher, redis_store)
        now = datetime.now(UTC)
        e = CanonicalEvent(
            vin=vin,
            ts=now,
            seq=1,
            tenant_id=1,
            oem="OEM_A",
            lat=13.08,
            lon=80.27,
            speed_kmh=10.0,
            soc_pct=10.0,
            soh_pct=70.0,
            odo_km=1.0,
            dtc=["P0A80"],
        )
        t0 = time.perf_counter()
        stats = await engine.process_batch([e])
        elapsed = time.perf_counter() - t0
        assert elapsed < 5.0
        assert stats.emitted == 4 and stats.persisted == 4 and stats.unknown_vehicle == 0
        assert sorted(a.type for a in publisher.alerts) == [
            "BATTERY_DTC",
            "LOW_SOC",
            "SOH_DROP",
            "STRAND_RISK",
        ]
        assert all(a.id is not None for a in publisher.alerts)
        # same event again: everything is suppressed by the Redis dedupe keys
        stats2 = await engine.process_batch(
            [e.model_copy(update={"seq": 2, "ts": now + timedelta(seconds=1)})]
        )
        assert stats2.emitted == 0 and stats2.suppressed == 4
        r = aioredis.Redis.from_url(orbit_env["redis"], decode_responses=True)
        try:
            assert 0 < await r.ttl(f"alert:dedupe:{vin}:LOW_SOC") <= 900
            assert await r.zcard(GEO_CHARGERS) >= 1
            assert await r.llen(f"soc:{vin}") == 1
        finally:
            await r.aclose()
        with psycopg.connect(orbit_env["postgres"]) as conn:
            rows = conn.execute(
                "SELECT type, severity, payload FROM alert WHERE vehicle_id = "
                "(SELECT id FROM vehicle WHERE vin = %s) ORDER BY type",
                (vin,),
            ).fetchall()
        assert [r[0] for r in rows] == ["BATTERY_DTC", "LOW_SOC", "SOH_DROP", "STRAND_RISK"]
        assert rows[3][1] == "CRITICAL" and rows[3][2]["remaining_range_km"] == 30.0
    finally:
        await repo.close()
        await redis_store.close()
