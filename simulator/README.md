# ORBIT vehicle telemetry simulator

Go service that emulates 100K connected vehicles from two OEM clouds and feeds
`telemetry.raw` (Kafka) or `fleet/{tenant_id}/{vin}` (MQTT, bridged by EMQX).
Payloads follow `contracts/oem-formats.md` exactly: odd tenants speak **OEM_A**
(flat, metric, ISO timestamps), even tenants **OEM_B** (nested, imperial, epoch
millis, battery as fraction).

## What it simulates

| Aspect | Behaviour |
|---|---|
| Fleet | Loaded from Postgres (`vehicle ⋈ vehicle_model ⋈ fleet ⋈ depot`, `LIMIT SIM_VEHICLES`). If the DB is unreachable it generates `SIM_VEHICLES` synthetic vehicles with valid ISO 3779 VINs around the 8 city centres in `seed.py`. |
| State machine | `PARKED → TRIP → PARKED → CHARGING`, plus `FUELING` for ICE. Trips mostly start 06:00–22:00 local (Asia/Kolkata), are a random walk along a heading (0–90 km/h, noise, traffic lights) inside a 0.35° box around the depot, then steer back to the depot. |
| Energy | kWh/km = battery/range, scaled by speed. SoC < 20 % ends the trip and starts a charging session: AC at the depot (`max_ac_kw`) or DC at a public fast charger (`max_dc_kw`, tapers above 80 %). Overnight depot top-ups. ICE vehicles send `fuel` / `fuel_level` instead of SoC. |
| Battery health | SoH slowly degrades per kWh charged; a 2 % cohort (`SIM_FAST_DEGRADE_PCT`) degrades 20× faster. |
| Events | `TRIP_START`, `TRIP_END`, `CHARGE_START`, `CHARGE_STOP`, `HARSH_BRAKE` (speed drop > 25 km/h or random), `DTC_SET`. |
| DTCs | `P0301`, `U0100` common; `P0AFA`, `P0A80` (battery) rarer and only on electrified models; clear after 1–6 h. |
| Ambient | Diurnal curve (20 °C at 03:00 → 32 °C at 15:00) plus per-vehicle bias. |
| Clock | `SIM_TIME_SCALE` speeds up the simulated clock (event `ts` is simulated time); physics uses the simulated Δt. |
| Noise | `SIM_DUP_PCT` duplicates (same `seq` re-sent 2–10 s later), `SIM_OOO_PCT` held back 2–10 s so a later `seq` overtakes, `SIM_MALFORMED_PCT` (default 0.1 %) bad check digit or missing `lat`/`pos` for the DLQ. The Kafka key is always the true VIN. |
| Burst | Every `SIM_BURST_EVERY_MIN` minutes (default 10) run at `SIM_BURST_FACTOR`× (3×) for `SIM_BURST_S` (60 s) — a shift start. |

Each vehicle has a monotonic `seq`; `(vin, seq)` is the idempotency key downstream.

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `SIM_VEHICLES` | `100000` | vehicles to simulate (also the DB `LIMIT`) |
| `SIM_RATE_HZ` | `1` | events per vehicle per second |
| `SIM_TRANSPORT` | `kafka` | `kafka` \| `mqtt` \| `stdout` (print `SIM_STDOUT_SAMPLE` payloads, then discard) \| `discard` |
| `SIM_DUP_PCT` / `SIM_OOO_PCT` / `SIM_MALFORMED_PCT` | `0.5` / `1.0` / `0.1` | noise rates in percent |
| `SIM_BURST_EVERY_MIN` / `SIM_BURST_S` / `SIM_BURST_FACTOR` | `10` / `60` / `3` | burst schedule; `SIM_BURST_EVERY_MIN=0` disables |
| `SIM_DURATION_S` | `0` | run time, `0` = forever (SIGINT/SIGTERM flush and exit) |
| `SIM_TIME_SCALE` | `1` | simulated seconds per real second |
| `SIM_SEED` | `42` | deterministic fleet + behaviour |
| `SIM_SHARDS` | `64` | worker goroutines; vehicle `i` lives on shard `i mod SIM_SHARDS` |
| `SIM_TENANTS` | `8` | tenants for the synthetic fallback |
| `SIM_TZ_OFFSET_MIN` | `330` | local offset for shift patterns |
| `SIM_METRICS_ADDR` | `:9101` | Prometheus `/metrics` and `/healthz` |
| `SIM_LOG_EVERY_S` | `5` | summary log interval |
| `SIM_KAFKA_TOPIC` / `SIM_KAFKA_ACKS` / `SIM_KAFKA_MAX_BUFFERED` | `telemetry.raw` / `leader` / `1000000` | producer tuning |
| `KAFKA_BOOTSTRAP` | `localhost:19092` | comma-separated brokers |
| `MQTT_URL` | `mqtt://localhost:1883` | MQTT broker |
| `DATABASE_URL` | (unset) | Postgres DSN for the seeded fleet |

## Running

```bash
# unit tests, vet, encoder micro-benchmark
make test vet bench-encode

# see ten payloads from a 50-vehicle fleet
make run-stdout

# 100K vehicles for 60 s into the discard sink; prints achieved msg/s
make bench

# same against the compose stack's Redpanda (localhost:19092)
make bench-kafka

# full stack: from the repo root
make run            # docker compose --profile sim up --build -d
```

Against the compose stack the container reads `.env` (`SIM_*`, `DATABASE_URL`)
and is pointed at `redpanda:9092` by `docker-compose.yml`.

## Throughput

Design for 100K msg/s on a laptop:

* 64 shards, each a goroutine owning ~1.5K vehicles, ticking on a jittered
  schedule (ticks of 10–100 ms, chunked so load is flat, no catch-up spiral).
* Hand-written JSON appenders (~300 ns, 0 allocs per payload); records are sliced
  out of 256 KiB per-shard arenas rather than allocated individually.
* franz-go with `ProducerBatchCompression(lz4)`, `ProducerLinger(20ms)`,
  1 MiB batches, `MaxBufferedRecords` back-pressure, sticky key partitioner
  (murmur2 on the VIN, so ordering per vehicle is preserved), leader acks.

Measured on an Apple M4 laptop (`SIM_TRANSPORT=discard`, `SIM_VEHICLES=100000`):

| Run | Result |
|---|---|
| 1 Hz, 20 s | **100,489 msg/s** sustained (rate-limited by design; 0 lag, 0 errors) |
| 1 Hz with 3× burst | ~300,000 msg/s during the burst |
| 5 Hz headroom, 12 s | 501,762 msg/s |
| `BenchmarkEncodeA/B` | ~310 ns/op, 0 B/op |

## Metrics

`sim_events_sent_total`, `sim_send_errors_total`, `sim_vehicles`,
`sim_buffered_records`, `sim_events_generated_total`, `sim_noise_total{kind}`,
`sim_burst_active`, plus Go runtime/process collectors. Every 5 s the log prints
`vehicles=… events/s=… sent=… errors=… buffered=…`, and a final `SUMMARY` line
with the achieved rate.

## Layout

```
cmd/simulator/        main: config → metrics → fleet load → transport → engine
internal/config       SIM_* environment parsing
internal/clock        scaled simulated clock, local hour
internal/vin          ISO 3779 check digit (mirror of orbit_common/vin.py)
internal/road         random walk, bounds, steering home
internal/fleet        vehicle state machine, Postgres + synthetic loaders
internal/oem          OEM_A / OEM_B encoders + malformed variants
internal/noise        dup / out-of-order / malformed decisions, delay queue
internal/transport    kafka (franz-go), mqtt (paho), stdout, discard
internal/metrics      Prometheus registry + /healthz
internal/engine       sharded worker pool, arenas, burst scheduler
```
