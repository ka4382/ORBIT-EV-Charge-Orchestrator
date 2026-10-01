import asyncio

from orbit_ingest.domain.processing import DtcTracker
from orbit_ingest.infrastructure.metrics import DTC_TOPK, dtc_topk_refresher, refresh_dtc_topk
from orbit_ingest.settings import IngestSettings


def _gauge_values() -> dict[str, float]:
    return {s.labels["code"]: s.value for m in DTC_TOPK.collect() for s in m.samples}


def test_refresh_sets_and_clears_labels():
    t = DtcTracker(k=3)
    t.record(["P0301", "P0301", "P0A80"])
    refresh_dtc_topk(t)
    assert _gauge_values() == {"P0301": 2.0, "P0A80": 1.0}
    refresh_dtc_topk(DtcTracker())
    assert _gauge_values() == {}


async def test_refresher_runs_until_stopped():
    t = DtcTracker()
    t.record(["U0100"])
    stop = asyncio.Event()
    task = asyncio.create_task(dtc_topk_refresher(t, stop, interval_s=0.01))
    await asyncio.sleep(0.03)
    stop.set()
    await task
    assert _gauge_values()["U0100"] == 1.0


def test_settings_env_prefix(monkeypatch):
    monkeypatch.setenv("INGEST_MAX_FUTURE_S", "42")
    assert IngestSettings().max_future_s == 42.0
    assert IngestSettings().canonical_topic == "telemetry.canonical"
