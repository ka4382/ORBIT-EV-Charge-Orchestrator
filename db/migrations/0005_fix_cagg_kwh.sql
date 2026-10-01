-- Fix telemetry_5m.kwh_charged. It was sum(charging_kw)/12, which multiplies by the number of samples in the
-- bucket. Energy in a 5-minute bucket is the mean power over the bucket times 5/60 h.
DROP MATERIALIZED VIEW IF EXISTS telemetry_5m;
CREATE MATERIALIZED VIEW telemetry_5m
WITH (timescaledb.continuous) AS
SELECT time_bucket('5 minutes', ts) AS bucket,
       vin, tenant_id,
       avg(soc_pct)   AS avg_soc,
       min(soc_pct)   AS min_soc,
       max(speed_kmh) AS max_speed,
       max(odo_km) - min(odo_km) AS km,
       avg(charging_kw) * (5.0 / 60.0) AS kwh_charged,
       count(*)       AS n
FROM telemetry
GROUP BY bucket, vin, tenant_id
WITH NO DATA;
SELECT add_continuous_aggregate_policy('telemetry_5m',
  start_offset => INTERVAL '1 hour', end_offset => INTERVAL '1 minute',
  schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE);
