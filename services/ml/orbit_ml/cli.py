"""Command-line entry points: train | publish | train-and-publish | evaluate."""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orbit_common.logging import configure

from . import models as m
from .data import Frame, load_live, load_live_range_labels, split_by_vehicle, synthetic_fleet
from .publish import publish

ARTIFACTS = Path(os.environ.get("ORBIT_ML_ARTIFACTS", Path(__file__).resolve().parents[1] / "artifacts"))
_REPO_DOCS = Path(__file__).resolve().parents[3] / "docs"
METRICS_COPY = _REPO_DOCS / "ml-metrics.json" if _REPO_DOCS.is_dir() else None

log = configure("ml")


def _connect() -> AbstractContextManager[Any]:  # pragma: no cover - exercised by the live run
    import psycopg
    from orbit_common.config import settings
    from psycopg.rows import dict_row

    return psycopg.connect(settings().database_url, row_factory=dict_row, autocommit=True)


def _redis() -> Any:  # pragma: no cover - exercised by the live run
    import redis
    from orbit_common.config import settings

    return redis.Redis.from_url(settings().redis_url)


def _live_labels(
    args: argparse.Namespace, connect: Callable[[], AbstractContextManager[Any]]
) -> Frame | None:
    if args.source == "synthetic":
        return None
    try:
        with connect() as conn:
            return load_live_range_labels(conn, until=datetime.now(UTC), hours=args.hours)
    except Exception as exc:  # live DB is optional for training; synthetic always works
        if args.source == "live":
            raise
        log.warning("live_labels_unavailable", error=str(exc))
        return None


def cmd_train(
    args: argparse.Namespace, connect: Callable[[], AbstractContextManager[Any]]
) -> m.TrainedModels:
    t0 = time.perf_counter()
    synthetic = synthetic_fleet(n_vehicles=args.vehicles, seed=args.seed)
    live = _live_labels(args, connect)
    models = m.train(synthetic, live, seed=args.seed)
    path = m.save(models, args.artifacts, args.metrics_copy)
    r, s = models.metrics["range"], models.metrics["soh"]
    log.info(
        "trained",
        artifact=str(path),
        seconds=round(time.perf_counter() - t0, 2),
        data_source=models.metrics["data_source"],
        range_mae_km=r["synthetic_test"]["model"]["mae_km"],
        range_baseline_mae_km=r["synthetic_test"]["baseline"]["mae_km"],
        soh_recall=s["synthetic_test"]["model"]["recall_lt80"],
        soh_baseline_recall=s["synthetic_test"]["baseline"]["recall_lt80"],
    )
    return models


def cmd_evaluate(args: argparse.Namespace) -> dict[str, Any]:
    models = m.load(args.artifacts)
    _, test = split_by_vehicle(synthetic_fleet(n_vehicles=args.vehicles, seed=args.seed))
    out = {"range": m.range_metrics(models, test), "soh": m.soh_metrics(models, test)}
    log.info("evaluated", trained_at=models.trained_at, **out)
    return out


def cmd_publish(
    args: argparse.Namespace,
    connect: Callable[[], AbstractContextManager[Any]],
    redis_factory: Callable[[], Any],
    models: m.TrainedModels | None = None,
) -> Any:
    models = models or m.load(args.artifacts)
    t0 = time.perf_counter()
    with connect() as conn:
        live = load_live(conn, until=datetime.now(UTC), hours=args.hours)
        log.info(
            "live_features", vehicles=len(live), hours=args.hours, seconds=round(time.perf_counter() - t0, 2)
        )
        result = publish(models, live, conn, redis_factory())
    log.info("published", seconds=round(time.perf_counter() - t0, 2), **result.__dict__)
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orbit_ml", description=__doc__)
    p.add_argument("command", choices=["train", "publish", "train-and-publish", "evaluate"])
    p.add_argument("--artifacts", type=Path, default=ARTIFACTS)
    p.add_argument("--metrics-copy", type=Path, default=METRICS_COPY)
    p.add_argument("--vehicles", type=int, default=6000, help="synthetic vehicles")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--hours", type=float, default=6.0, help="live telemetry window")
    p.add_argument(
        "--source",
        choices=["auto", "synthetic", "live"],
        default="auto",
        help="range training data: auto = synthetic, plus live labelled vehicles when there are enough",
    )
    return p


def main(
    argv: Sequence[str] | None = None,
    connect: Callable[[], AbstractContextManager[Any]] = _connect,
    redis_factory: Callable[[], Any] = _redis,
) -> int:
    args = parser().parse_args(argv)
    if args.command == "train":
        cmd_train(args, connect)
    elif args.command == "evaluate":
        cmd_evaluate(args)
    elif args.command == "publish":
        cmd_publish(args, connect, redis_factory)
    else:
        cmd_publish(args, connect, redis_factory, cmd_train(args, connect))
    return 0
