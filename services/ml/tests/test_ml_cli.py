from __future__ import annotations

import json

import fakeredis
import pytest
from ml_fakes import StubConn, agg_row
from orbit_ml import cli


def _args(tmp_path, *extra: str) -> list[str]:
    return [
        "--artifacts",
        str(tmp_path / "a"),
        "--metrics-copy",
        str(tmp_path / "m.json"),
        "--vehicles",
        "600",
        *extra,
    ]


def test_train_evaluate_publish(tmp_path):
    def broken():
        raise ConnectionError("no db")

    assert cli.main(["train", *_args(tmp_path)], connect=broken) == 0  # auto: falls back to synthetic
    metrics = json.loads((tmp_path / "m.json").read_text())
    assert metrics["data_source"]["range_train"] == "synthetic"
    assert cli.main(["evaluate", *_args(tmp_path)]) == 0

    conn = StubConn(agg=[agg_row(f"VIN{i:014d}", i + 1) for i in range(3)])
    redis = fakeredis.FakeRedis(decode_responses=True)
    assert cli.main(["publish", *_args(tmp_path)], connect=lambda: conn, redis_factory=lambda: redis) == 0
    assert len(redis.keys("range:*")) == 3 and len(conn.inserted) == 3


def test_train_and_publish_and_source_flags(tmp_path):
    conn = StubConn(agg=[agg_row(f"VIN{i:014d}", i + 1) for i in range(2)])
    redis = fakeredis.FakeRedis(decode_responses=True)
    rc = cli.main(
        ["train-and-publish", *_args(tmp_path, "--source", "synthetic")],
        connect=lambda: conn,
        redis_factory=lambda: redis,
    )
    assert rc == 0 and len(redis.keys("range:*")) == 2

    def broken():
        raise ConnectionError("no db")

    with pytest.raises(ConnectionError):
        cli.main(["train", *_args(tmp_path, "--source", "live")], connect=broken)


def test_train_with_live_labels(tmp_path):
    conn = StubConn(agg=[agg_row("VIN00000000000001", 1)], agg_second=[agg_row("VIN00000000000001", 1)])
    models = cli.cmd_train(cli.parser().parse_args(["train", *_args(tmp_path)]), connect=lambda: conn)
    assert models.metrics["data_source"]["live_labelled_vehicles"] == 1
    assert models.metrics["data_source"]["range_train"] == "synthetic"  # 1 vehicle < minimum
