-- Telemetry hypertable: time + space partitioned, compressed, retained 30 days (archiver moves to Parquet).
CREATE TABLE IF NOT EXISTS telemetry (
  ts              TIMESTAMPTZ NOT NULL,
  vin             CHAR(17) NOT NULL,               -- deliberate denormalisation: no join on write path
  seq             BIGINT NOT NULL,
  tenant_id       INT NOT NULL,
  lat             DOUBLE PRECISION NOT NULL,
  lon             DOUBLE PRECISION NOT NULL,
  speed_kmh       REAL NOT NULL,
  soc_pct         REAL,
  soh_pct         REAL,
  odo_km          DOUBLE PRECISION NOT NULL,
  ambient_c       REAL,
  charging_state  SMALLINT NOT NULL DEFAULT 0,    -- 0 NONE, 1 AC, 2 DC
  charging_kw     REAL NOT NULL DEFAULT 0,
  dtc             TEXT[] NOT NULL DEFAULT '{}',
  evt             TEXT
);
SELECT create_hypertable('telemetry', 'ts', chunk_time_interval => INTERVAL '1 hour',
                         partitioning_column => 'vin', number_partitions => 16, if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS telemetry_vin_seq_uidx ON telemetry (vin, seq, ts);   -- idempotent replay
CREATE INDEX IF NOT EXISTS telemetry_vin_ts_idx ON telemetry (vin, ts DESC);
CREATE INDEX IF NOT EXISTS telemetry_low_soc_idx ON telemetry (tenant_id, ts DESC) WHERE soc_pct < 30;  -- partial

ALTER TABLE telemetry SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'vin',
  timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('telemetry', INTERVAL '24 hours', if_not_exists => TRUE);
SELECT add_retention_policy('telemetry', INTERVAL '30 days', if_not_exists => TRUE);

-- 5-minute continuous aggregate feeding dashboards and the SoC-drop rule
CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_5m
WITH (timescaledb.continuous) AS
SELECT time_bucket('5 minutes', ts) AS bucket,
       vin, tenant_id,
       avg(soc_pct)   AS avg_soc,
       min(soc_pct)   AS min_soc,
       max(speed_kmh) AS max_speed,
       max(odo_km) - min(odo_km) AS km,
       sum(charging_kw) / 12.0 AS kwh_charged,       -- 5 min at 1 Hz ≈ kw * 5/60
       count(*)       AS n
FROM telemetry
GROUP BY bucket, vin, tenant_id
WITH NO DATA;
SELECT add_continuous_aggregate_policy('telemetry_5m',
  start_offset => INTERVAL '1 hour', end_offset => INTERVAL '1 minute',
  schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE);
