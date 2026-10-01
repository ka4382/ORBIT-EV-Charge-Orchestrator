# orbit-state

Consumes `telemetry.canonical` (group `state`) and keeps the hot vehicle state in Redis per `contracts/redis.md`:
`HSET veh:{vin}` (VehicleState fields, `dtc` as JSON, null fields omitted, TTL 600 s), `SADD tenant:{id}:vins`,
`GEOADD geo:vehicles:{tenant}` and `PUBLISH events:{tenant}` with `{"type":"state"}` throttled to one message per
vehicle per 2 s. `status` = CHARGING if charging, else AT_RISK if soc < 15, else DRIVING if speed > 2, else IDLE
(OFFLINE is derived by readers from `ts` age). Stale / out-of-order events (older `ts`, or same `ts` and `seq` <=
stored) are skipped; the last-seen (seq, ts) is cached in memory and back-filled from Redis on first sight.
Every 5 s a per-tenant summary (`summary:{tenant}` hash, TTL 10 s) is written and published as `{"type":"summary"}`;
`kwh_today` integrates `charging_kw` over the gap between consecutive events (capped at 60 s), reset per UTC day.
All writes go through non-transactional pipelines of at most `STATE_PIPELINE_MAX_COMMANDS` (1000) commands.

Layout: `domain/` (status, staleness, throttle, fleet tracker), `application/` (batch service, loop, summary ticker),
`infrastructure/redis_store.py`. Config: shared env (`KAFKA_BOOTSTRAP`, `REDIS_URL`) plus `STATE_*` (`settings.py`).
Run: `python -m orbit_state`. Test: `cd services && uv run pytest state`; integration: `uv run pytest -m integration tests/integration`.
