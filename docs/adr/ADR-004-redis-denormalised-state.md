# ADR-004: Redis holds a denormalised latest-state cache rebuilt from Kafka (cache invalidation strategy)

**Status:** Accepted · 2026-10-01

## Context
The console needs "where is every vehicle now" for 100K vehicles at sub-100 ms API latency. Querying the
hypertable for the latest row per VIN is a heavy `DISTINCT ON` even with indexes (see docs/sql-optimisation.md).

## Decision
The state service writes one Redis hash per VIN (`veh:{vin}`), a GEO set per tenant and a 5-second fleet summary.
Keys carry a 600 s TTL so vehicles that stop reporting simply disappear (no explicit invalidation). Writes
are accepted only when the incoming `seq` is newer than the stored one, which makes out-of-order delivery
harmless. A cold start replays `telemetry.canonical` from the last 10 minutes to warm the cache.

Also denormalised on purpose: `alert.payload` is JSONB (one table for all alert types) and `telemetry.vin`
duplicates `vehicle.vin` to keep the write path join-free.

## Consequences
+ p95 API latency for state and map endpoints is dominated by network, not SQL.
+ Invalidation is time-based and monotonic, so there is no cache-coherence protocol to get wrong.
− Redis is a second source of truth for "now"; the database remains the source of truth for history.
− Memory: ~1 KB × 100K = ~100 MB, well under the 1 GB limit configured.
