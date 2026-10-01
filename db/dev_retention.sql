-- Laptop disk guard: keep 6 hours of raw telemetry instead of 30 days (production policy is in 0002).
-- Applied by `make dev-retention`; never run in production.
SELECT remove_retention_policy('telemetry', if_exists => TRUE);
SELECT add_retention_policy('telemetry', INTERVAL '6 hours');
