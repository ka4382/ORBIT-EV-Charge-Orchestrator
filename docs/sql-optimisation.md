# SQL optimisation — EXPLAIN ANALYZE before and after

Measured on the seeded dataset (8 tenants, 100,000 vehicles) in the local `timescale/timescaledb-ha:pg16`
container, with the simulator streaming. Reproduce with `bash tests/sql/explain.sh`, which writes the raw
plans to `tests/reports/explain/`.

Dataset for the run: 100,000 vehicles, 400,000 alerts seeded by the script (95 % acknowledged, tagged and
deleted afterwards), live telemetry from the simulator. Times are `Execution Time` from `EXPLAIN (ANALYZE,
BUFFERS)`; full plans are in `tests/reports/explain/*.txt`.

| # | Query | Before (ms) | After (ms) | Change made |
|---|---|---|---|---|
| Q1 | Alert feed, page 50 of 50 rows for one tenant | 89.4 | 0.55 | `OFFSET` pagination → keyset on `(ts, id)` with composite index `alert (tenant_id, ts DESC, id DESC)` |
| Q2 | Open CRITICAL alerts per tenant (dashboard badge) | 122.2 | 1.16 | Partial index `alert (tenant_id, severity) WHERE acked_ts IS NULL`, giving an index-only scan |
| Q3 | Vehicle list page with model and OEM names | 10.9 | 2.9 | Correlated subqueries per row (ORM N+1 shape) → one join plus keyset on `vehicle (fleet_id, id)` |
| Q4 | Latest state of every vehicle in a fleet | 804 | < 1 (Redis `MGET`) | Not fixable with an index at this volume: moved to the Redis projection (ADR-004) |

## What the plans show

**Q1.** Before: a parallel sort of every alert in the tenant (`Sort Key: ts DESC, id DESC`, top-N heapsort) to
throw away 2,450 rows. After: `Index Scan using alert_feed_idx` with `Index Cond: (tenant_id = 1) AND
(ROW(ts, id) < ROW(...))` reads exactly 50 rows. Cost is now independent of page depth. The API's cursor is
an opaque base64 of `(ts, id)`.

**Q2.** Before: a full scan and sort over the alert table to count a handful of rows. After: `Index Only Scan
using alert_open_idx`. The partial index only holds unacknowledged alerts, so it stays small as history grows.

**Q3.** Before: `Index Scan using vehicle_pkey` walks 3,050 rows, and for each one runs two subqueries
(`loops=3050` on `vehicle_model_pkey`), which is the classic N+1 shape an ORM produces with lazy loading.
After: one join, and `Index Only Scan using vehicle_fleet_idx` to reach the page. In the API the keyset
cursor is passed directly, so even the 3,000-row skip in this benchmark disappears.

**Q4.** `DISTINCT ON (vin) ... ORDER BY vin, ts DESC` over 10 minutes of telemetry sorts ~39K rows per worker
(`Sort Key: t.vin, t.ts DESC`) and takes ~0.8 s even with `telemetry_vin_ts_idx`. The console needs this for
every vehicle every few seconds, so the answer is architectural: the state service keeps the latest value per
VIN in Redis and the API reads a page with one `MGET`.

## Other optimisations in the schema

- `telemetry` is a hypertable chunked hourly and space-partitioned by `vin` (16), compressed after 24 h
  (segment by `vin`), retained 30 days. A continuous aggregate `telemetry_5m` serves dashboards and the SoC
  drop rule without scanning raw rows.
- Partial index `telemetry (tenant_id, ts DESC) WHERE soc_pct < 30` keeps low-charge lookups small.
- Unique index `telemetry (vin, seq, ts)` makes replays idempotent; the archiver COPYs on the happy path and
  only falls back to `INSERT ... ON CONFLICT DO NOTHING` via a temp table when a batch hits a duplicate.
- `charging_slot (charger_id, start_ts)` serves the depot Gantt; `charging_plan (tenant_id, created_ts DESC, id DESC)`
  serves plan lists and today's cost totals.

