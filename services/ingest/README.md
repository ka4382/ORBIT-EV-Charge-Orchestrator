# orbit-ingest

Consumes `telemetry.raw` (OEM_A / OEM_B JSON from the simulator), normalises each payload through
`orbit_common.adapters`, rejects malformed / unknown-OEM / invalid / future-dated (> 5 min) events to
`telemetry.dlq` as `{"reason", "detail", "raw"}`, drops duplicates with a 10-minute rolling Bloom filter on
`(vin, seq)`, and produces `telemetry.canonical` keyed by VIN. Offsets are committed only after every send in
a batch is acknowledged (at-least-once; downstream is idempotent). Back-pressure pauses partitions when un-acked
producer bytes exceed `INGEST_BUFFER_HIGH_WATER_BYTES` and bounds in-flight batches with a semaphore.

Layout: `domain/` (decode + DTC Count-Min sketch), `application/` (batch pipeline, consume/commit loop),
`infrastructure/` (aiokafka adapters, `orbit_dtc_topk{code}` gauge refreshed every 10 s), `__main__.py` wiring.
Config: shared env from `orbit_common.config` (`KAFKA_BOOTSTRAP`, `METRICS_PORT`) plus `INGEST_*` (see `settings.py`).
Run: `python -m orbit_ingest`. Test: `cd services && uv run pytest ingest`; integration: `uv run pytest -m integration tests/integration`.
