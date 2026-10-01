# Benchmarks and capacity

All numbers measured on one laptop: Apple M4, Docker Desktop with 10 CPUs and 8 GB, everything in docker
compose (broker, databases, services, simulator). Reproduce with the commands under each table.

## 1. Producer and broker: 100K events/s

| Run | Vehicles | Rate | Result |
|---|---|---|---|
| Simulator → Redpanda, steady | 100,000 | 1 Hz each | **100,384 events/s, 0 errors** over 20 s, ~2.3K records buffered |
| Simulator, shift-start burst | 100,000 | 3x for 60 s | ~299K events/s generated |
| Simulator encode only (discard sink) | 100,000 | 1 Hz | 100,489 events/s, 0 B/op encoders (311 ns/event) |

`cd simulator && make bench` · `SIM_TRANSPORT=kafka SIM_VEHICLES=100000 make run`

## 2. Stream services: measured per-replica throughput

Measured from each replica's `orbit_events_in_total` counter, 20 s windows, while draining a backlog
(so each replica runs flat out on one core).

| Service | Per replica (1 core) | Main per-event cost after tuning |
|---|---|---|
| ingest (adapter, validation, Bloom dedupe, produce) | ~24,000 events/s | pydantic validation (~6 µs), aiokafka fetch |
| state (Redis projection) | ~18,000 events/s | redis-py command packing |
| alert (rules, GEOSEARCH, Postgres) | ~30,000 events/s | rule context, Redis dedupe |
| archiver (COPY to Timescale, Parquet) | ~30,000 events/s | row formatting |

### What tuning changed (profile-driven)

| Change | Before | After |
|---|---|---|
| `telemetry.raw` created with 1 partition by an early producer (broker auto-create) | 1 ingest replica usable | 48 partitions via a `topics` bootstrap job; auto-create disabled |
| Ingest: per-record `send` (~9 µs) → `send_batch` per partition (~0.6 µs) | ~12K/s per replica | ~21K/s |
| Bloom filter on numpy scalars → `bytearray`, probes hashed once for both generations | 5.8 µs | 3.8 µs |
| VIN check digit and VIN→partition cached (a fleet repeats the same VINs every second) | — | ~24K/s per ingest replica |
| State: 14-field HSET + EXPIRE per vehicle → one `SET veh:{vin} <json> EX 600`; GEOADD/SADD batched per tenant; hiredis parser | ~5K/s | ~18K/s |
| Rebalance-safe commits (`orbit_common.kafka.safe_commit`) | crash loop when replicas were added | scale-out works with no code change |

## 3. Sizing for 100K events/s

Cores needed at 100K/s = 100,000 / per-replica rate, plus headroom to absorb a 3x burst through lag:

| Service | Replicas for 100K/s | Helm HPA (min / max) |
|---|---|---|
| ingest | 5 | 3 / 12 |
| state | 6 | 2 / 8 |
| alert | 4 | 2 / 8 |
| archiver | 4 | 2 / 6 |

That is about 19 cores for the stream tier, which does not fit beside the broker and databases on a
10-core laptop. Two consequences, stated plainly:

- **Laptop demo default is 20,000 vehicles (20K events/s, 60K/s bursts)**, which the full pipeline sustains
  in real time with lag near zero (section 4). Set `SIM_VEHICLES=100000` to reproduce the 100K/s producer and
  broker numbers above.
- **100K/s end to end is a cluster deployment**: the Helm chart's HPA ranges cover the replica counts above,
  and partition counts (48) allow up to 48 replicas per group without repartitioning.

A run at 100K/s on the laptop also showed the failure mode: with the broker limited to 2 cores, a 3x burst
plus four consumer groups saturated Redpanda, the simulator's buffer filled (1M records) and producing
paused. Nothing was lost (acks, at-least-once), but latency targets were missed. The dev broker now gets 3
cores.

## 4. Live pipeline at 20K events/s

Full stack in docker compose: simulator (20,000 vehicles, 1 Hz, 0.5 % duplicates, 1 % out-of-order,
0.1 % malformed, 3x burst for 60 s every 10 min), 2 ingest, 2 state, 2 alert, 1 archiver, planner, API, web.

### Latency (30 s window at steady state, from each service's `orbit_e2e_seconds` histogram)

| Hop measured (vehicle timestamp → processed) | p50 | p95 | p99 | Target |
|---|---|---|---|---|
| ingest (validated, deduped, on `telemetry.canonical`) | ≤ 50 ms | ≤ 50 ms | ≤ 5 s ¹ | — |
| state (vehicle visible in Redis, served to the console) | ≤ 50 ms | ≤ 100 ms | ≤ 250 ms | dashboard < 2 s |
| alert (rule fired, row in Postgres, pushed on pub/sub) | ≤ 250 ms | ≤ 250 ms | ≤ 250 ms | critical alert < 5 s |

¹ The simulator delays 1 % of events by 2–10 s on purpose (out-of-order injection), and those count from
their original vehicle timestamp. The console additionally throttles WebSocket pushes to one per vehicle per
2 s, so a vehicle on screen is at most ~2.1 s old.

### Consumer lag at steady state

Under 1,100 events per group (well under one second of traffic) for ingest, state and alert over 80 s of
monitoring.

### 3x burst (60K events/s for 60 s)

Kafka absorbs the burst; nothing is dropped. With 2 ingest replicas (~48K/s) the burst built 662K events of
lag that drained in ~45 s. A single alert replica fell ~980K behind and needed several minutes; with 2 alert
replicas (now the default) the same backlog drained in ~25 s. This is the "survive a 3x burst without data
loss" requirement: bursts become lag, not loss.

### Chaos: broker killed for 20 s (`tests/chaos/kill_broker.sh`)

| Measure | Result |
|---|---|
| Records the simulator buffered while the broker was down | 345,994 |
| Records dropped by the producer | **0** |
| Consumers still running afterwards | 7/7 |
| Raw events before → after | 58,697,903 → 61,907,148 |
| Canonical events before → after | 57,546,557 → 60,911,793 |
| Time from broker restart to lag < 20K on every group | 125 s (includes ~40 s partition leader election) |

The first chaos runs failed, and each failure fixed a real defect:

1. The simulator gave up on records after 10 retries, so a 20 s outage dropped 353,408 events. It now retries
   until a 5-minute delivery timeout, with `MaxBufferedRecords` providing back-pressure.
2. Ingest exits on a produce timeout (crash-only, offsets uncommitted) but Compose had no restart policy, so
   ingest stayed down. Every long-running service now has `restart: unless-stopped` (Kubernetes restarts pods
   anyway).

## 5. API latency under load (`tests/load/api_latency.py`)

300 virtual users, each with its own token (the API rate-limits 100 req/min per user), 60 s, cycling through
the dashboard endpoints. Postgres holds the 100,000-vehicle seed. `tests/reports/api_latency.json` holds the run.

| Config | Requests/s | p50 | p95 | p99 | Target |
|---|---|---|---|---|---|
| 2 uvicorn workers | 417 | 4.3 ms | 84 ms | 1,644 ms | p95 < 200 ms, p99 < 500 ms |
| **4 uvicorn workers (default now)** | **448** | **4.3 ms** | **29 ms** | **77 ms** | met |

With 2 workers both processes were CPU-bound and the tail queued; the API is stateless, so the fix is more
workers or replicas (Helm runs 3+ API pods with HPA). Per endpoint at 4 workers, p95 ranged 27–30 ms and p99
74–82 ms. State and range-forecast calls returned 404 in this run because the simulator was stopped (no live
vehicle state), so they are excluded from the percentiles.

## 6. Laptop disk guard (incident)

During the session the host disk filled up (2 GB free) and Docker's VM went read-only. Cause: the topic
contract keeps raw telemetry 24 h and canonical 7 d, which at 20K events/s is hundreds of GB. Fixes:

- `KAFKA_RETENTION_MS_MAX` and `KAFKA_RETENTION_BYTES_PER_PARTITION` cap retention below the contract; the
  `topics` job re-applies them on every start (`.env` uses 30 min and 100 MB per partition).
- `ARCHIVER_SAMPLE_DIVISOR=20` and `ARCHIVER_PARQUET_SAMPLE_DIVISOR=10` on a laptop; both are 1 in production.
- `make dev-retention` keeps 6 h of telemetry in Timescale instead of 30 days.
- `make run` now runs a preflight that refuses to start with less than 15 GB free on the host.
- The disk filled a second time after the images were rebuilt with only ~19 GB free, so the laptop default
  is now 5,000 vehicles and 50 MB per Kafka partition. Use 20,000 vehicles for the measurements above only
  when the host has 40 GB or more free.

