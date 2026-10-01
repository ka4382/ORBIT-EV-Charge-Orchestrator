# orbit-alert

Consumes `telemetry.canonical` (group `alert`) and runs the pure rule engine in `domain/rules.py` over
(event, context): LOW_SOC (soc < 20, WARN), STRAND_RISK (soc < 15 and no charger within soc/100 x nominal range
via `GEOSEARCH geo:chargers`, CRITICAL), SOC_DROP (> 40 pts within 2 h from a 24 x 5-min ring buffer mirrored to
`soc:{vin}`), SOH_DROP (soh < 80), BATTERY_DTC (any `orbit_common.dtc.BATTERY_CODES`, CRITICAL) and
CHARGE_INTERRUPTED (AC/DC -> NONE within 60 s while soc < 80 and last kW > 0). Repeats are suppressed for 15 min via
`SET alert:dedupe:{vin}:{type} NX EX 900`. Surviving alerts are inserted into Postgres `alert` (one `unnest` INSERT
per batch, `RETURNING id`), produced to Kafka `alerts` (key `tenant_id:vin`, payload = the API alert item) and
published to `events:{tenant}` as `{"type":"alert"}`. Nominal range and vehicle_id come from a lazily loaded
vin -> (id, nominal_range_km) cache (chunked `WHERE vin = ANY(...)`, default range 300 km, unknown VINs are still
emitted to Kafka/pub-sub but not persisted). `geo:chargers` / `charger:{id}` are loaded from Postgres at startup if
missing.

Layout: `domain/` (alerts, rules, history), `application/` (engine, cache, loop), `infrastructure/` (redis, postgres,
kafka). Config: shared env (`KAFKA_BOOTSTRAP`, `REDIS_URL`, `DATABASE_URL`) plus `ALERT_*` (`settings.py`).
Run: `python -m orbit_alert`. Test: `cd services && uv run pytest alert`; integration: `uv run pytest -m integration tests/integration`.
