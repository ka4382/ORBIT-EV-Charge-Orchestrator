"""Planner entry point.

python -m orbit_planner                      consume `plans.requested` (group `planner`), plan, commit
python -m orbit_planner --nightly --tenant 1 plan every BEV of a tenant for 06:00 tomorrow, smooth depots
python -m orbit_planner --nightly --all      same for every tenant
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
from datetime import UTC, datetime, time

import orjson
from orbit_common import kafka as kafka_common
from orbit_common import metrics
from orbit_common.config import settings
from orbit_common.logging import configure
from prometheus_client import Histogram

from .application.nightly import NightlyPlanner
from .application.plan_vehicle import PlannerConfig, PlanVehicle
from .domain.models import PlanRequest
from .infrastructure.kafka_bus import TOPIC_REQUESTED, KafkaRedisPublisher
from .infrastructure.postgres import PostgresPlanRepository
from .infrastructure.redis_store import RedisStateStore

PLAN_SECONDS = Histogram(
    "orbit_planner_seconds",
    "time to compute + persist one charging plan",
    ["mode"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
log = configure("planner")


def parse_request(raw: bytes) -> PlanRequest:
    d = orjson.loads(raw)
    depart = d["depart_ts"]
    depart_ts = (
        datetime.fromisoformat(str(depart).replace("Z", "+00:00"))
        if isinstance(depart, str)
        else datetime.fromtimestamp(float(depart), tz=UTC)
    )
    return PlanRequest(
        plan_id=int(d["plan_id"]),
        vin=str(d["vin"]).upper(),
        tenant_id=int(d["tenant_id"]),
        target_soc=float(d.get("target_soc", 90)),
        depart_ts=depart_ts,
        vehicle_id=int(d["vehicle_id"]) if d.get("vehicle_id") is not None else None,
    )


async def _wiring() -> tuple[PostgresPlanRepository, RedisStateStore, KafkaRedisPublisher, PlanVehicle]:
    s = settings()
    repo = PostgresPlanRepository(s.database_url)
    store = RedisStateStore(s.redis_url)
    publisher = KafkaRedisPublisher(store)
    await repo.start()
    await publisher.start()
    return repo, store, publisher, PlanVehicle(repo, store, publisher, PlannerConfig())


async def _teardown(
    repo: PostgresPlanRepository, store: RedisStateStore, publisher: KafkaRedisPublisher
) -> None:
    for closer in (publisher.stop, store.stop, repo.stop):
        with contextlib.suppress(Exception):
            await closer()


async def consume(stop: asyncio.Event) -> None:
    await kafka_common.wait_for_kafka()
    repo, store, publisher, planner = await _wiring()
    consumer = kafka_common.consumer(TOPIC_REQUESTED, group="planner", max_poll_records=50)
    await consumer.start()
    log.info("planner.started", topic=TOPIC_REQUESTED)
    try:
        while not stop.is_set():
            batches = await consumer.getmany(timeout_ms=1000)
            if not batches:
                continue
            for tp, msgs in batches.items():
                for m in msgs:
                    metrics.EVENTS_IN.labels("planner", tp.topic).inc()
                    try:
                        req = parse_request(m.value)
                    except Exception as exc:  # malformed request: log and skip, never block the partition
                        metrics.EVENTS_DROPPED.labels("planner", "bad_request").inc()
                        log.warning("planner.bad_request", error=str(exc))
                        continue
                    with PLAN_SECONDS.labels("stream").time():
                        try:
                            out = await planner.execute(req)
                            log.info(
                                "planner.done",
                                plan_id=out.plan_id,
                                vin=out.vin,
                                status=out.status,
                                reason=out.reason or None,
                            )
                        except Exception as exc:
                            log.error("planner.failed", plan_id=req.plan_id, vin=req.vin, error=str(exc))
                            with contextlib.suppress(Exception):
                                await repo.mark_failed(req.plan_id)
            await kafka_common.safe_commit(consumer)
    finally:
        await consumer.stop()
        await _teardown(repo, store, publisher)
        log.info("planner.stopped")


async def nightly(tenants: list[int] | None, depart: time, target_soc: float, dry_run: bool) -> int:
    repo, store, publisher, planner = await _wiring()
    try:
        ids = tenants or await repo.list_tenants()
        runner = NightlyPlanner(repo, planner)
        for tid in ids:
            with PLAN_SECONDS.labels("nightly").time():
                rep = await runner.run(tid, depart_local=depart, target_soc=target_soc, persist=not dry_run)
            log.info(
                "planner.nightly",
                tenant_id=tid,
                planned=rep.planned,
                failed=rep.failed,
                infeasible=rep.infeasible,
                total_cost=rep.total_cost,
                baseline_cost=rep.baseline_cost,
                savings=rep.savings,
                kwh=rep.total_kwh,
                failures=rep.failures,
                depots=[
                    {
                        "depot_id": d.depot_id,
                        "vehicles": d.vehicles,
                        "capacity_kw": d.capacity_kw,
                        "peak_before": d.peak_kw_before,
                        "peak_after": d.peak_kw_after,
                        "moves": d.moves,
                        "feasible": d.feasible,
                    }
                    for d in rep.depots
                ],
            )
        return 0
    finally:
        await _teardown(repo, store, publisher)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="orbit_planner", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--nightly", action="store_true", help="one-shot: plan every BEV for tomorrow's departure"
    )
    ap.add_argument("--tenant", type=int, action="append", help="tenant id (repeatable); default all tenants")
    ap.add_argument("--all", action="store_true", help="all tenants (default when --tenant is omitted)")
    ap.add_argument("--depart", default="06:00", help="local departure time HH:MM (default 06:00)")
    ap.add_argument("--target-soc", type=float, default=90.0)
    ap.add_argument("--dry-run", action="store_true", help="compute and log, do not write plans")
    args = ap.parse_args(argv)

    if args.nightly:
        hh, mm = (int(x) for x in args.depart.split(":"))
        return asyncio.run(nightly(args.tenant, time(hh, mm), args.target_soc, args.dry_run))

    metrics.serve()
    stop = asyncio.Event()

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        await consume(stop)

    asyncio.run(runner())
    return 0


if __name__ == "__main__":
    sys.exit(main())
