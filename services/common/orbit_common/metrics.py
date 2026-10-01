"""Prometheus metrics server + shared metric objects. One HTTP endpoint per service on METRICS_PORT."""

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from .config import settings

EVENTS_IN = Counter("orbit_events_in_total", "events consumed", ["service", "topic"])
EVENTS_OUT = Counter("orbit_events_out_total", "events produced", ["service", "topic"])
EVENTS_DROPPED = Counter("orbit_events_dropped_total", "events dropped", ["service", "reason"])
BATCH_LATENCY = Histogram(
    "orbit_batch_seconds",
    "batch processing time",
    ["service"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
E2E_LATENCY = Histogram(
    "orbit_e2e_seconds",
    "vehicle ts to service-processed",
    ["service"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
CONSUMER_LAG = Gauge("orbit_consumer_lag", "approximate consumer lag", ["service", "topic", "partition"])


def serve(port: int | None = None) -> None:
    start_http_server(port or settings().metrics_port)
