from orbit_common import kafka


class _Capture:
    def __init__(self, *args, **kw):
        self.args, self.kw = args, kw


def test_consumer_caller_overrides_defaults(monkeypatch):
    monkeypatch.setattr(kafka, "AIOKafkaConsumer", _Capture)
    c = kafka.consumer("telemetry.canonical", group="g", max_poll_records=500)
    assert c.args == ("telemetry.canonical",)
    assert c.kw["max_poll_records"] == 500  # override wins, no duplicate-kwarg TypeError
    assert c.kw["group_id"] == "g" and c.kw["enable_auto_commit"] is False


def test_producer_caller_overrides_defaults(monkeypatch):
    monkeypatch.setattr(kafka, "AIOKafkaProducer", _Capture)
    p = kafka.producer(linger_ms=5)
    assert p.kw["linger_ms"] == 5
    assert p.kw["acks"] == "all" and p.kw["enable_idempotence"] is True and p.kw["compression_type"] == "lz4"


class _RebalancingConsumer:
    def __init__(self, owned, fail=None):
        self.owned, self.fail, self.commits = set(owned), fail, []

    def assignment(self):
        return self.owned

    async def commit(self, offsets=None):
        if self.fail:
            raise self.fail
        self.commits.append(offsets)


async def test_safe_commit_drops_revoked_partitions():
    c = _RebalancingConsumer({"p0"})
    assert await kafka.safe_commit(c, {"p0": 10, "p1": 20}) == 1
    assert c.commits == [{"p0": 10}]


async def test_safe_commit_tolerates_rebalance_errors():
    from aiokafka.errors import CommitFailedError, IllegalStateError

    for err in (IllegalStateError("not assigned"), CommitFailedError("rebalanced")):
        assert await kafka.safe_commit(_RebalancingConsumer({"p0"}, err), {"p0": 1}) == 0
    assert await kafka.safe_commit(_RebalancingConsumer(set()), {"p0": 1}) == 0
    assert await kafka.safe_commit(_RebalancingConsumer({"p0"}, IllegalStateError("x"))) == 0
    assert await kafka.safe_commit(_RebalancingConsumer({"p0"})) == 1


def test_topic_configs_caps_retention_from_env(monkeypatch):
    spec = {"partitions": 48, "retention_ms": 604800000}
    monkeypatch.delenv("KAFKA_RETENTION_MS_MAX", raising=False)
    monkeypatch.delenv("KAFKA_RETENTION_BYTES_PER_PARTITION", raising=False)
    assert kafka.topic_configs(spec) == {"retention.ms": "604800000"}
    monkeypatch.setenv("KAFKA_RETENTION_MS_MAX", "1800000")
    monkeypatch.setenv("KAFKA_RETENTION_BYTES_PER_PARTITION", "104857600")
    assert kafka.topic_configs(spec) == {"retention.ms": "1800000", "retention.bytes": "104857600"}
    assert kafka.topic_configs({"retention_ms": 600000})["retention.ms"] == "600000"  # cap never raises it
