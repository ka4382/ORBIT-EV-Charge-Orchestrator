# orbit-archiver

Consumes `telemetry.canonical` (group `archiver`) and persists it twice. Hot path: rows are formatted as COPY text
(`charging_state` NONE/AC/DC -> 0/1/2, `dtc` as a Postgres array literal) and loaded into the `telemetry`
hypertable every 1 s or 5000 rows with a single `COPY ... FROM STDIN`. Replays trip the unique index
`(vin, seq, ts)`, which aborts a COPY; the batch is then rolled back and re-loaded through a per-transaction TEMP
table followed by `INSERT ... ON CONFLICT (vin, seq, ts) DO NOTHING`, so the happy path stays at COPY speed and only
batches that really contain duplicates pay for the conflict check (rows repeated inside one batch are collapsed in
memory first). Kafka offsets are committed after each Postgres flush. Cold path: every event is buffered per
(UTC day, hour, tenant) and written as zstd Parquet to `s3://{S3_BUCKET}/dt=YYYY-MM-DD/hour=HH/tenant_id={t}/part-{uuid}.parquet`
every 10 min, when `ARCHIVER_PARQUET_MAX_ROWS` is reached, and on shutdown (bucket is created if missing). Parquet is
best effort: offsets are not held for it, so a crash loses at most one Parquet window (rebuildable from Timescale/Kafka).
`ARCHIVER_SAMPLE_DIVISOR=10` keeps only `seq % 10 == 0` in Postgres while Parquet still receives everything.

Layout: `domain/rows.py` (COPY/Parquet formatting), `application/` (buffering + flush policy, loop),
`infrastructure/` (psycopg pool COPY loader, boto3 store). Config: shared env (`KAFKA_BOOTSTRAP`, `DATABASE_URL`,
`S3_*`) plus `ARCHIVER_*` (`settings.py`). Run: `python -m orbit_archiver`. Test: `cd services && uv run pytest archiver`;
integration: `uv run pytest -m integration tests/integration`.
