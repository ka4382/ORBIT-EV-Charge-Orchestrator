# Redis key schema (all keys prefixed by nothing; single logical DB; AP cache rebuildable from Kafka)

| Key | Type | TTL | Written by | Read by |
|---|---|---|---|---|
| `veh:{vin}` | STRING: VehicleState JSON document, written with `SET ... EX` | 600 s | state | api, planner |
| `tenant:{tenant_id}:vins` | SET | none | state | api (fleet listing fallback) |
| `geo:vehicles:{tenant_id}` | GEO (member = vin) | none | state | api (map viewport), alert |
| `geo:chargers` | GEO (member = charger_id) | none | api at startup / seed | alert (STRAND_RISK), planner |
| `charger:{id}` | HASH {name, max_kw, price_per_kwh, is_public, tenant_id} | none | api at startup | alert, planner |
| `soc:{vin}` | LIST of "ts:soc" capped 24 entries (5-min buckets) | 7200 s | alert | alert (SOC_DROP) |
| `range:{vin}` | STRING JSON `{km_per_soc_pct, method:"hgb_v1", mae_km, trained_at}` | 1 day | ml | api (range-forecast) |
| `ratelimit:{sub}` | STRING counter | 60 s | api | api |
| `summary:{tenant_id}:part:{replica}` | HASH partial fleet summary for the partitions one state replica owns (incl. `soc_n`) | 10 s | state | api (merges) |
| `summary:{tenant_id}:parts` | SET of live replica ids | 60 s | state | api |
| `alert:dedupe:{vin}:{type}` | STRING | 900 s | alert | alert (suppress repeats for 15 min) |
| pub/sub `events:{tenant_id}` | channel | — | state, alert | api WS fan-out |

## Why `veh:{vin}` is a JSON string, not a HASH

The first version stored VehicleState as a 14-field HASH plus an EXPIRE. Profiling the state service at
100K events/s showed most of its CPU in redis-py command packing (about 30 arguments per vehicle). One
`SET key <json> EX 600` carries the same data in 5 arguments, sets the TTL atomically, and lets readers use
`MGET` for a page of vehicles. GEOADD and SADD are batched per tenant per consumer batch.

## Fleet summary with several state replicas

Each state replica only consumes some partitions, so it can only count the vehicles it sees. Every 5 s a
replica writes its partial summary and registers itself in `summary:{tenant}:parts`. The API sums the
partials (`avg_soc` weighted by `soc_n`), then adds `open_alerts`, `cost_today` and `savings_today` from
Postgres, and pushes the merged summary to WebSocket clients. Partials expire after 10 s, so a replica
that dies or loses its partitions drops out of the total.
