# EV Charge Orchestrator — End-to-End Production Plan

Hackathon: Connected Vehicle Intelligence (Motorq case study, Talenciaglobal)
Problem space: EV charging and battery health (mixed ICE/EV fleets)
Plan date: 2026-10-01

This plan maps every requirement in the Problem Statement PDF (sections 6–14) to a concrete
piece of work, and every section of the Solution Document Template to the evidence that fills it.

---

## 0. One-paragraph pitch

Fleet managers running mixed EV fleets need a way to decide **when, where and how much each vehicle
should charge** because uncoordinated charging strands vehicles mid-shift, pays peak tariffs, and
hides battery degradation until a pack fails. The EV Charge Orchestrator ingests 100K vehicles of
telemetry in real time, predicts range and battery state-of-health, computes a cost-optimal charging
schedule per vehicle under time-of-use tariffs, routes low-charge vehicles to the nearest reachable
charger, and lets an AI agent recommend and (with approval) apply actions with an audit trail.

---

## 1. Problem statement and validation  (Template §2)

**Statement (template format):**
"An EV fleet manager needs a way to schedule and route charging across hundreds of vehicles because
uncoordinated charging causes stranded vehicles, peak-tariff spend and unnoticed battery degradation,
which today costs an estimated 15–30% of energy spend and 1–3% of daily vehicle availability."

- Primary user: Fleet manager (EV / mixed fleet)
- Secondary: Fleet finance, OEM battery warranty teams, drivers, sustainability team

**Evidence table (fill with sources during Phase 0):**

| Evidence / Assumption | Source or Method | What it shows | Confidence |
|---|---|---|---|
| Time-of-use tariffs vary 2–3x between peak and off-peak | Public utility tariff sheets (e.g. TNEB, PG&E EV rates) | Scheduling has a direct cost lever | High |
| Uncoordinated depot charging creates demand-charge spikes | Published fleet electrification studies (NREL, ICCT) | Peak shaving is a measurable saving | High |
| Battery SoH degrades faster with frequent 100% charges and DC fast charging | Battery degradation literature | SoH-aware scheduling extends pack life | Medium |
| A vehicle below 15% SoC with no reachable charger is a "strand event" costing a tow + lost shift | Assumption, validated via simulation | Justifies stranding alerts | Medium |
| Connected car generates up to 25 GB/hour by 2030 | McKinsey / S&P (cited in PDF) | Edge filtering and stream design needed | High |

**Existing alternatives and why they fall short:**
- OEM portals: single-brand, no cross-fleet optimisation.
- Aftermarket dongles: hardware cost, no battery-level signals.
- Motorq Fuse: flags idling and cost, not charge scheduling under tariffs.
- Charger-network apps: charger-centric, not fleet-centric; no SoH.

**Success metrics (template §2.3):**

| Metric | Baseline (simulated, no orchestration) | Target | How measured |
|---|---|---|---|
| Energy cost per kWh charged | naive "charge on arrival" | −20% | Back-test simulator against DP schedule |
| Strand events per 1,000 vehicles / day | naive threshold | −70% | Simulation with alerts on vs off |
| Peak depot load (kW) | uncoordinated | −30% | Sum of concurrent sessions |
| Vehicles with SoH < 80% detected before failure | none | 90% recall | Labelled synthetic degradation |
| Ingest→alert latency | n/a | < 5 s | Trace timestamps |

Scale of impact: at 10K vehicles ≈ X ₹/year, at 100K ≈ 10X (compute in doc with tariff assumptions).

---

## 2. Feature list (MoSCoW)  (Template §4)

| ID | Feature | Priority | Phase |
|---|---|---|---|
| F-01 | 100K-vehicle Go simulator: trips, SoC drain, DTCs, bursts, duplicates, out-of-order | Must | 1 |
| F-02 | MQTT → Kafka ingestion, Avro schema validation, idempotent dedupe, back-pressure | Must | 1 |
| F-03 | Real-time vehicle state (Redis) + telemetry hypertable (TimescaleDB) | Must | 1 |
| F-04 | Low-SoC / stranding-risk alert < 5 s via WebSocket | Must | 2 |
| F-05 | Nearest reachable charger (Dijkstra with range constraint) | Must | 2 |
| F-06 | Cost-optimal charging schedule (DP over tariff slots, depot capacity) | Must | 2 |
| F-07 | Secure REST API: OAuth2/JWT, RBAC, tenant isolation, keyset pagination, rate limits | Must | 2 |
| F-08 | React dashboard: live map, vehicle drawer, charging timeline, alert feed | Must | 3 |
| F-09 | Batch analytics: nightly Parquet compaction, cost and SoH reports | Must | 3 |
| F-10 | Range prediction + SoH estimation model vs linear baseline | Should | 4 |
| F-11 | pgvector similarity: "vehicles with similar degradation profile" | Should | 4 |
| F-12 | AI agent: answers questions, recommends actions, human approval, audit log | Should | 4 |
| F-13 | Privacy: location masking by role, retention job, right-to-erasure flow | Must | 3 |
| F-14 | Observability: OpenTelemetry traces, Prometheus metrics, Grafana dashboards, Loki logs | Must | 3 |
| F-15 | Carbon report per route (grid intensity × kWh) | Could | 5 |
| F-16 | Multi-OEM adapter layer (2 simulated OEM formats → canonical) | Should | 1 |

---

## 3. Architecture  (Template §5.1)

### 3.1 C4 Level 1 — System context
Actors: Fleet manager (web), Fleet finance (reports), Driver (push alert, future), Auditor.
External: OEM clouds (simulated), Identity provider (Keycloak), LLM API (Claude), Tariff/grid feed (static file), Object storage.

### 3.2 C4 Level 2 — Containers

```
[Simulator (Go)] --MQTT/mTLS--> [EMQX] --bridge--> [Kafka/Redpanda: telemetry.raw]
                                                        |
                              [Ingest svc (Py)] <-------+   validate Avro, dedupe (Bloom), normalise (OEM adapters)
                                    | produce
                              [Kafka: telemetry.canonical]  (key = vin, 48 partitions)
                                    |
        +---------------------------+---------------------------+
        |                           |                           |
 [State svc]                 [Alert svc]                 [Archiver svc]
 Redis latest state          sliding-window rules        micro-batch → TimescaleDB hypertable
 geohash index               strand risk, SoH drop       hourly → Parquet on MinIO/S3
        |                           |                           |
        +------------> [Kafka: alerts] -----> [API svc (FastAPI)] ---WS/HTTPS---> [React web]
                                                   |
                       [PostgreSQL+Timescale+pgvector]  [Redis]  [MinIO]  [Keycloak]
                                                   |
                              [Planner svc] Dijkstra + DP schedule (on demand + nightly)
                              [ML svc] range/SoH model, embeddings
                              [Agent svc] LangGraph + tools + audit
                              [Batch (DuckDB/Spark job)] nightly analytics over Parquet
```

Protocols on arrows: MQTT (TLS 1.3, mTLS), Kafka (SASL/TLS), HTTPS/JSON, WebSocket, gRPC optional between planner and API.

### 3.3 Data flow of one event with latency budget

| Hop | Budget |
|---|---|
| Vehicle → EMQX → Kafka raw | 50 ms |
| Ingest validate + dedupe + canonical | 100 ms |
| State svc → Redis | 50 ms |
| Alert svc rule → alerts topic | 200 ms |
| API WS push → browser | 100 ms |
| **Total ingest → dashboard** | **< 1 s (target < 2 s)** |
| Critical alert end to end | < 5 s |

---

## 4. Technology stack and justification  (Template §5.2)

| Layer | Choice | Why, and what was rejected |
|---|---|---|
| Device protocol | MQTT via EMQX | IoT-native, mTLS per device, QoS 1. Rejected raw HTTP (no back-pressure, no persistent session). |
| Messaging | Kafka API via Redpanda (dev) / Kafka or MSK/Confluent (cloud) | Partitioned, durable, replayable. Redpanda single binary for docker compose. Rejected RabbitMQ (no replay, weaker partition ordering). |
| Schema | Avro + schema registry | Compact, evolvable, enforced. Rejected raw JSON (no evolution contract). |
| Stream processing | Python asyncio consumers (aiokafka), one service per concern | Simple to test, horizontally scalable via consumer groups. Rejected Flink (operational weight for hackathon), kept as future ADR. |
| Batch | Parquet on MinIO (S3 API) + DuckDB (dev) / Spark (cloud) | Cloud-agnostic object storage; DuckDB queries billions of rows locally. |
| Relational + time-series + vector | PostgreSQL 16 + TimescaleDB + pgvector | One engine gives 3NF core (CP), hypertable compression/partitioning, and vectors. Rejected separate Cassandra + Qdrant (more ops, same rubric credit). |
| NoSQL / cache | Redis 7 | Latest-state hash per VIN, GEO index for chargers, rate limiting, pub/sub for WS fan-out. AP by design. |
| Search (optional) | OpenSearch | Alert/DTC full-text search; only if time permits. |
| Backend | FastAPI + SQLModel + Pydantic | Async, OpenAPI for free, fast to test. Rejected Spring Boot (slower iteration for a small team). |
| Simulator | Go | Needs 100K goroutines producing 100K msg/s; Python cannot reach this. |
| Frontend | React + Vite + TypeScript + MapLibre GL + Recharts | Map-first UI. |
| ML | scikit-learn (GradientBoosting), evaluated vs linear baseline | Explainable, fast to train on synthetic data. |
| Agent | LangGraph + Claude API + MCP-style tool definitions | Tool whitelisting, state machine for approval gate. |
| Auth | Keycloak (OIDC) | Standard OAuth2/OIDC, RBAC claims, tenant realm. |
| Secrets | Docker secrets locally; Vault or cloud secret manager via External Secrets in K8s | Rubric requires vault. |
| Observability | OpenTelemetry → Prometheus, Grafana, Loki, Tempo | Open standard, no vendor lock-in. |
| Infra | Docker, Helm, Terraform (AWS EKS module; GKE module stub), GitHub Actions | Cloud-agnostic: same Helm chart on any K8s. |

---

## 5. Data architecture  (Template §5.3, PDF §8)

### 5.1 Relational core (3NF) — PostgreSQL

```
tenant(id, name, created_at)
fleet(id, tenant_id→tenant, name, depot_id→depot)
depot(id, tenant_id, name, lat, lon, grid_capacity_kw)
vehicle(id, vin UNIQUE, tenant_id, fleet_id→fleet, oem_id→oem, model_id→vehicle_model, battery_capacity_kwh, created_at)
vehicle_model(id, oem_id→oem, name, powertrain ENUM(ICE,HEV,PHEV,BEV), nominal_range_km)
oem(id, name, payload_format)
driver(id, tenant_id, name_masked, licence_hash)
driver_assignment(id, driver_id→driver, vehicle_id→vehicle, start_ts, end_ts)
trip(id, vehicle_id→vehicle, driver_id, start_ts, end_ts, start_geohash, end_geohash, km, kwh_used)
charger(id, tenant_id NULLABLE, depot_id NULLABLE, lat, lon, max_kw, connector ENUM, is_public)
tariff(id, tenant_id, name, timezone)
tariff_slot(id, tariff_id→tariff, dow, start_min, end_min, price_per_kwh)
charging_plan(id, vehicle_id, created_ts, target_soc, depart_ts, total_cost, status)
charging_slot(id, plan_id→charging_plan, charger_id→charger, start_ts, end_ts, kw, kwh, cost)
charging_session(id, vehicle_id, charger_id, start_ts, end_ts, kwh, cost, plan_slot_id NULLABLE)
alert(id, tenant_id, vehicle_id, type, severity, ts, payload_jsonb, acked_by, acked_ts)
subscription(id, tenant_id, plan, starts, ends, status)
user_account(id, tenant_id, idp_subject, role ENUM(admin,manager,finance,auditor))
audit_log(id, tenant_id, actor_type ENUM(user,agent,system), actor_id, action, resource, ts, details_jsonb)
battery_health(id, vehicle_id, computed_ts, soh_pct, cycles, method)
```

Deliberate denormalisation (documented in ADR-4):
- `vehicle_state` in Redis (latest SoC, lat/lon, speed, ts) — hot read path, rebuildable from Kafka.
- `alert.payload_jsonb` — flexible alert context, avoids one table per alert type.
- `telemetry` hypertable stores `vin` directly rather than `vehicle_id` to avoid a join on the write path.

### 5.2 Telemetry hypertable (TimescaleDB)

```
telemetry(ts timestamptz, vin char(17), seq bigint, lat, lon, speed_kmh, soc_pct, odo_km,
          dtc text[], evt text, tenant_id int)
  hypertable on ts, chunk 1 hour; space partition on hash(vin) 16 ways
  unique index (vin, seq)  -- idempotency on replay
  compression after 24 h (segment by vin, order by ts desc)
  retention: drop chunks > 30 days (moved to Parquet by archiver)
continuous aggregate: telemetry_5m (vin, bucket, avg soc, max speed, km) — feeds dashboards
```

### 5.3 Polyglot map with CAP choice

| Data | Store | CAP | Reason |
|---|---|---|---|
| Fleet, vehicle, tariff, plan, session, billing, audit | PostgreSQL | CP | Ownership and money need ACID |
| Raw + canonical telemetry stream | Kafka | AP (durable) | Replay, decoupling, at-least-once |
| Latest vehicle state, geo index, rate limits | Redis | AP | Sub-ms reads, rebuildable |
| Time-series telemetry (hot 30 d) | TimescaleDB | CP (single writer) with async replica for reads | Range queries, continuous aggregates |
| Warm/cold telemetry (> 30 d) | Parquet on MinIO/S3 | AP | Cheap, batch-scannable |
| Battery-profile embeddings | pgvector | CP | Similarity search co-located with vehicle |
| Alert search (optional) | OpenSearch | AP | Full-text |

PACELC note: telemetry path chooses availability and low latency over consistency (EL/PA); planning and billing choose consistency (EC/PC).

### 5.4 Capacity estimate and lifecycle

| Item | Value |
|---|---|
| Vehicles | 100,000 |
| Events/s | 100,000 sustained, 300,000 burst for 5 min |
| Event size | ~1 KB JSON, ~250 B Avro, ~60 B compressed Parquet |
| Kafka retention | 7 days raw ≈ 100K × 250 B × 86,400 × 7 ≈ 15 TB (dev: 1 h) |
| Hot (Timescale) | 30 days compressed ≈ 100K × 60 B × 86,400 × 30 ≈ 15 TB (dev: down-sample 1/10) |
| Warm (Parquet, 90 d) | ≈ 45 TB |
| Cold (Parquet Glacier tier, 2 y) | ≈ 360 TB |
| Cost sketch | S3 standard ~$0.023/GB-mo, Glacier ~$0.004/GB-mo → document per-tier monthly cost |

Partition keys: Kafka key = vin (ordering per vehicle, 48 partitions); Timescale = ts + hash(vin); Parquet = dt=YYYY-MM-DD/hour=HH/tenant_id.
Hot-spot avoidance: hash(vin), never tenant_id as the shard key (skewed tenants).

### 5.5 Query optimisation targets (three queries for EXPLAIN ANALYZE)

1. "Latest state for all vehicles in fleet X" — before: correlated subquery on telemetry; after: Redis or `DISTINCT ON (vin) ... ORDER BY vin, ts DESC` with index (vin, ts desc).
2. "Vehicles whose SoC dropped > 40% in the last 2 h" — before: full scan; after: continuous aggregate + partial index `WHERE soc_pct < 30`.
3. "Alerts feed, page 50 for tenant" — before: OFFSET pagination; after: keyset on (ts, id) with composite index (tenant_id, ts desc, id).
Also show removal of an ORM N+1 (vehicle → model → oem) with `selectinload`.

---

## 6. Interfaces and contracts  (Template §6.4)

### 6.1 Canonical telemetry event (Avro, subject `telemetry.canonical-value`, v1)

```json
{"vin":"1HGCM82633A004352","ts":"2026-09-25T10:15:02.120Z","seq":88412,
 "tenant_id":12,"lat":21.1702,"lon":72.8311,"speed_kmh":64.2,"soc_pct":41.0,
 "soh_pct":93.5,"odo_km":18234.7,"dtc":["P0301"],"evt":"HARSH_BRAKE",
 "charging":{"state":"NONE","kw":0},"oem":"OEM_A","schema_version":1}
```

Evolution rule: add-only optional fields with defaults; registry compatibility = BACKWARD.

### 6.2 Kafka topics

| Topic | Key | Partitions | Retention | Consumers |
|---|---|---|---|---|
| telemetry.raw | vin | 48 | 24 h | ingest |
| telemetry.canonical | vin | 48 | 7 d | state, alert, archiver, ml |
| telemetry.dlq | vin | 6 | 7 d | ops |
| alerts | tenant_id:vin | 12 | 7 d | api (WS), agent |
| plans.requested / plans.ready | vehicle_id | 12 | 1 d | planner, api |

### 6.3 REST API (OpenAPI generated by FastAPI), versioned `/api/v1`

```
POST /auth/token                       (Keycloak proxied)
GET  /vehicles?fleet_id&cursor&limit   keyset pagination
GET  /vehicles/{vin}/state             Redis
GET  /vehicles/{vin}/telemetry?from&to&cursor
GET  /vehicles/{vin}/range-forecast    ML svc
GET  /vehicles/{vin}/nearest-chargers  Dijkstra
POST /plans                            create charging plan (async → plans.requested)
GET  /plans/{id}
GET  /alerts?since&cursor              keyset
POST /alerts/{id}/ack
GET  /reports/cost?from&to             batch (Parquet)
POST /agent/query                      agent, returns answer + proposed actions
POST /agent/actions/{id}/approve       human approval, audited
DELETE /privacy/drivers/{id}           right-to-erasure (async job)
WS   /ws/fleet/{fleet_id}              live state + alerts
```
Error format: RFC 7807 problem+json. Rate limit: token bucket in Redis, 100 req/min per user, 429 with Retry-After.

### 6.4 Sequence diagrams to draw (Mermaid in /docs)
1. Happy path: event → alert → WS → UI ack.
2. Failure: Kafka broker killed mid-stream; producers buffer, consumer group rebalances, no loss (idempotent producer, acks=all).
3. Failure: duplicate event with same (vin, seq) → dropped by Bloom filter, then by unique index on replay.
4. OEM_B format arrives with unknown field → adapter maps known fields, unknown to `extras`, DLQ only on schema-invalid.

---

## 7. Algorithms and data structures  (Template §6.5, PDF §9)

| Problem | Algorithm | Complexity | Where |
|---|---|---|---|
| Nearest reachable charger under remaining range | Dijkstra on road graph (nodes = grid/OSM-like synthetic graph, edges = km & energy cost), early exit when cost > range | O((V+E) log V) | planner/routing.py |
| Optimal charging schedule under time-of-use tariffs | DP: state = (slot t, SoC bucket s), transition = charge k kW or idle, cost = price[t]×kWh; constraint: SoC ≥ target by departure; depot capacity via greedy post-pass per depot | O(T × S × K) per vehicle | planner/schedule.py |
| Trip / stop segmentation of noisy GPS | DP change-point on speed + distance (or HMM-lite), min stop 3 min | O(n) | stream/trips.py |
| VIN validation | Regex `^[A-HJ-NPR-Z0-9]{17}$` + check-digit (position 9) | O(1) | common/vin.py |
| DTC parsing | Regex `^[PCBU][0-3][0-9A-F]{3}$` + lookup table | O(1) | common/dtc.py |
| Dedupe at 100K/s | Bloom filter per partition keyed (vin,seq), 10-min rolling, FP 0.1% | O(k) | ingest/dedupe.py |
| Top-K vehicles by DTC frequency | Count-Min Sketch + heap | O(1) update | stream/topk.py |
| Sliding-window SoC drop | Ring buffer per VIN, 2 h window | O(1) amortised | alert/rules.py |
| Charger proximity | Geohash (precision 6) + Redis GEO | O(log n) | state/geo.py |
| Clusters of vehicles at unapproved sites | Union-Find on geohash adjacency | O(n α(n)) | batch/clusters.py |

Each gets pseudocode, complexity annotation, and a measured runtime at scale (e.g. Dijkstra on 50K-node graph, DP for 100K vehicles × 96 slots).

---

## 8. AI / ML component  (Template §11)

**Range and SoH model**
- Features: SoC trajectory, speed profile, ambient temp (simulated), charge-cycle count, fast-charge ratio, odometer, model.
- Target 1: remaining range (km). Baseline: nominal_range × SoC. Model: GradientBoostingRegressor. Metric: MAE, compare.
- Target 2: SoH (%) with synthetic degradation curve. Baseline: linear by cycles. Metric: MAE + recall on SoH<80.
- Leakage check: split by vehicle, not by time slice.

**Vector layer (pgvector)**
- Embedding = 32-dim summary of each vehicle's last 30 days (SoC histogram, charge pattern, temp exposure).
- Use: "find vehicles similar to this degrading one" → early warning cohort.

**Agent (LangGraph + Claude)**
- Tools (read): get_fleet_summary, get_vehicle_state, get_plan, get_cost_report.
- Tools (write, gated): propose_replan(vehicle_ids, target_soc) → creates `agent_action` row with status PENDING; human approves via API; only then planner runs.
- Guardrails: tool allowlist per role, tenant scoping injected server-side (never from prompt), prompt-injection filter on telemetry-derived text, max 5 tool calls, cost cap per request, every prompt/tool call/response written to audit_log.
- Evaluation: 20 golden questions with expected numbers; measure exact-match and hallucination rate.
- Cost/latency per request logged.

---

## 9. Security and compliance  (Template §8, PDF §11)

**STRIDE top 5 (ingestion + public API)**

| Threat | Category | Control |
|---|---|---|
| Spoofed vehicle publishes fake SoC | Spoofing | mTLS per device cert (EMQX), VIN bound to cert CN |
| Replayed / duplicated events | Tampering | seq + Bloom + unique index; reject ts skew > 5 min |
| Tenant reads another tenant's vehicles | Info disclosure | tenant_id from JWT claim, enforced in repository layer + Postgres RLS |
| API flood | DoS | Redis token bucket, Kafka back-pressure, pod HPA |
| Agent tricked into replanning wrong fleet | Elevation | Server-side tenant scoping, approval gate, audit |

- AuthN/Z: Keycloak OIDC, JWT RS256, roles admin/manager/finance/auditor, Postgres row-level security by tenant.
- Transport: TLS 1.3 everywhere, mTLS device→broker, SASL/TLS to Kafka.
- At rest: AES-256 (Postgres volume encryption / cloud KMS), MinIO SSE.
- Secrets: never in repo; `.env.example`; Vault or cloud secret manager via External Secrets Operator.
- Privacy: location precision reduced to geohash-5 for finance/auditor roles; driver names stored masked; retention job drops raw > 30 d; `DELETE /privacy/drivers/{id}` cascades and writes an erasure audit record; DPDP and GDPR mapping table in doc.
- Scans in CI: Semgrep (SAST), Trivy (image + deps), OWASP ZAP baseline (DAST against compose stack), gitleaks.

---

## 10. Non-functional targets and how we prove them  (Template §7)

| NFR | Target | How we measure |
|---|---|---|
| Throughput | 100K ev/s sustained, 300K for 5 min | Go simulator + k6 Kafka extension; Grafana consumer-lag panel; screenshot |
| Latency | ingest→dashboard < 2 s; alert < 5 s | OTel trace from simulator ts to WS send; histogram |
| API | p95 < 200 ms, p99 < 500 ms | k6 HTTP scenario, 500 VUs |
| Scalability | add consumer pod → throughput rises, no code change | Demo scaling ingest 1→3 replicas |
| Availability | kill broker/pod, recover, no loss | Chaos script: `docker kill redpanda`, count events before/after |
| Soak | 1 h at 50% load, no memory growth | Grafana memory panel |

Honesty rule: report laptop numbers as measured; if 100K/s is reached only with down-sampled persistence, say so in the doc.

---

## 11. Test strategy  (Template §9, PDF §12)

| Type | Tools | Scope | CI |
|---|---|---|---|
| Unit | pytest + coverage, Go test | algorithms, adapters, rules, repositories; 80%+ line | yes |
| Integration | Testcontainers (Redpanda, Postgres, Redis) | ingest→canonical, alert rule, planner persistence | yes |
| Contract | Pact (api ↔ web), schema-registry compatibility check | topic and REST contracts | yes |
| Acceptance | behave (Gherkin) | "stranding alert", "cost-optimal plan", "erasure" | yes |
| Load | k6 (HTTP + xk6-kafka), Go simulator | 100K/s, p95/p99, lag | nightly job |
| Security | Semgrep, Trivy, ZAP baseline, gitleaks | fail on critical/high | yes |
| Chaos | bash + pytest | kill broker/pod, assert recovery and counts | nightly |
| Compliance | pytest | audit rows for each data access and agent action; erasure cascades | yes |

Edge cases: duplicate (vin,seq), out-of-order seq, ts in future, unknown OEM field, invalid VIN, negative SoC, broker outage, Redis flush (state rebuild from Kafka).

---

## 12. Observability  (Template §10)

- OpenTelemetry SDK in every service; trace id propagated through Kafka headers.
- Metrics: events_in/s, dedupe_dropped/s, consumer_lag, alert_latency_seconds, api_latency, planner_duration, agent_cost_usd.
- Dashboards (Grafana JSON committed in /infra/grafana): Ingest, API, Planner, Agent.
- Logs: structured JSON → Loki. Traces → Tempo.
- Troubleshooting walkthrough for the doc: latency spike → lag panel → trace waterfall → slow query log.

---

## 13. DevOps and repository  (Template §5.4, §14; PDF §13)

### 13.1 Repository structure (two levels)

```
ev-charge-orchestrator/
├── README.md
├── docker-compose.yml            # one-command full stack + simulator
├── Makefile                      # make run / test / load / seed
├── .env.example
├── .github/workflows/            # ci.yml, nightly-load.yml, release.yml
├── simulator/                    # Go: cmd/, internal/fleet, internal/oem, internal/mqtt
├── services/
│   ├── common/                   # Python pkg: domain models, avro schemas, vin/dtc, telemetry client
│   ├── ingest/                   # MQTT bridge consumer → validate → dedupe → canonical
│   ├── state/                    # Redis state + geo
│   ├── alert/                    # rules engine
│   ├── archiver/                 # Timescale writer + Parquet
│   ├── planner/                  # Dijkstra + DP
│   ├── ml/                       # training, inference, embeddings
│   ├── agent/                    # LangGraph, tools, audit
│   └── api/                      # FastAPI, auth, WS, routers
├── web/                          # React + Vite
├── batch/                        # DuckDB/Spark jobs, cost & SoH reports
├── infra/
│   ├── helm/                     # umbrella chart + subcharts
│   ├── terraform/                # aws/ (EKS, MSK, RDS, S3), gcp/ (stub)
│   ├── grafana/                  # dashboards
│   └── keycloak/                 # realm export
├── docs/
│   ├── PLAN.md
│   ├── adr/                      # ADR-001 … ADR-005
│   ├── architecture/             # C4, ER, sequence (Mermaid + PNG)
│   ├── threat-model.md
│   ├── sql-optimisation.md       # EXPLAIN before/after
│   └── solution-document.docx
└── tests/
    ├── unit/  integration/  contract/  acceptance/  load/  chaos/  security/
```

Each Python service follows hexagonal layering: `api/` (routers, DTOs), `application/` (use cases), `domain/` (entities, rules), `infrastructure/` (repositories, kafka, redis). Import-linter enforces direction.

### 13.2 CI pipeline (GitHub Actions, every push)
1. lint (ruff, mypy, golangci-lint, eslint) → 2. unit (py + go + web) with coverage gate 80% → 3. build images → 4. Trivy + Semgrep + gitleaks → 5. integration (Testcontainers) → 6. acceptance (behave against compose) → 7. ZAP baseline → 8. Helm lint + `terraform validate`. Nightly: load + chaos + soak.

### 13.3 Cloud-agnostic deployment
- Same Helm chart runs on kind (local), EKS, GKE, AKS. Only `values-<cloud>.yaml` differs (storage class, ingress class, secret backend).
- Terraform: `infra/terraform/aws` provisions EKS + S3 + RDS(Postgres) + MSK; `gcp` module skeleton shows parity.
- No cloud SDK calls in application code; object storage via S3 API (MinIO locally).

---

## 14. Architecture Decision Records (write these five)

1. ADR-001 Kafka-compatible streaming (Redpanda dev / Kafka prod) over RabbitMQ.
2. ADR-002 PostgreSQL + TimescaleDB + pgvector as unified CP store vs Cassandra + Qdrant.
3. ADR-003 Telemetry path is AP / at-least-once with idempotent consumers; billing and plans are CP (CAP trade-off).
4. ADR-004 Redis as denormalised latest-state cache rebuilt from Kafka (cache invalidation strategy).
5. ADR-005 Python asyncio consumers now, Flink later (operational simplicity vs exactly-once).

---

## 15. Work plan

Assumes a team of 3–4 and roughly two weeks. Compress by dropping "Should/Could" first. Every phase ends with a green CI run and a tagged commit.

### Phase 0 — Day 1: framing and scaffolding
- Confirm problem statement, users, metrics, evidence sources (this document §1).
- Create repo, folder structure, compose skeleton with Redpanda, Postgres/Timescale, Redis, MinIO, Keycloak, Grafana stack.
- CI skeleton: lint + unit + coverage gate.
- Draw C4 L1/L2 and ER diagram (Mermaid).
- DoD: `docker compose up` brings up infra; `make test` passes an empty suite.

### Phase 1 — Days 2–4: firehose
- Go simulator: 100K vehicles, trip state machine, SoC drain by speed and temp, DTC injection, charging events, 2 OEM payload formats, bursts, duplicates, out-of-order, MQTT publish with client certs.
- Ingest service: Avro schema, OEM adapters (Adapter pattern), VIN/DTC regex, Bloom dedupe, DLQ, back-pressure (pause/resume consumer on lag).
- State service (Redis hash + GEO), archiver (batched COPY into hypertable; hourly Parquet).
- Seed script: fleets, depots, chargers, tariffs, 100K vehicles.
- Tests: unit for adapters/vin/dtc/bloom; integration ingest→canonical with Testcontainers.
- DoD: 100K vehicles streaming, lag panel stable, data landing in Timescale and Parquet.

### Phase 2 — Days 5–7: decisions and API
- Alert service: sliding-window rules (SoC < 15% and no reachable charger, SoC drop rate, SoH drop, DTC battery codes). Publish to alerts topic.
- Planner: synthetic road graph (grid with random weights or OSM extract), Dijkstra with range cut-off; DP schedule over 15-min slots × tariff; depot capacity smoothing.
- API: Keycloak integration, JWT middleware, RLS, keyset pagination, rate limit, WS fan-out via Redis pub/sub, RFC 7807 errors, OpenAPI.
- Tests: unit for rules/Dijkstra/DP with complexity notes; integration for planner persistence; contract tests; first BDD scenarios.
- DoD: alert visible over WS < 5 s; plan created and retrievable; auth enforced across tenants.

### Phase 3 — Days 8–10: UI, batch, privacy, observability, evidence
- React dashboard: MapLibre map with vehicle clusters and chargers, vehicle drawer (SoC, range, SoH), charging timeline Gantt, alert feed with ack, cost report page.
- Batch: nightly DuckDB job → cost per fleet, SoH trend, strand events; carbon per route if time.
- Privacy: role-based location masking, retention job, erasure endpoint + audit.
- Observability: OTel in all services, Grafana dashboards committed, Loki/Tempo.
- Evidence: k6 load run, chaos script (kill Redpanda and ingest pod), EXPLAIN ANALYZE before/after for 3 queries, coverage report, Semgrep/Trivy/ZAP reports.
- DoD: end-to-end demo path works on live stream; NFR table filled with measured numbers.

### Phase 4 — Days 11–12: ML, vector, agent
- Train range/SoH model on simulator output with labelled degradation; compare to baseline; persist metrics.
- Embeddings into pgvector; "similar vehicles" endpoint and UI panel.
- Agent: LangGraph graph, tools, approval gate, audit, golden-question eval.
- Tests: model eval test with threshold, agent tool-permission tests, audit assertions.
- DoD: agent answers "which vehicles should charge tonight and what does it save" with numbers from real tables and a pending action a manager approves.

### Phase 5 — Days 13–14: hardening and submission
- Helm chart + kind deployment; Terraform AWS apply (or plan output if no budget); values for a second cloud.
- Solution document: fill every template section from this plan and measured evidence; screenshots.
- ADRs, STRIDE doc, SBOM (syft), declarations of open source and AI tools.
- Demo video per template timeline (0:00 problem, 0:30 pitch, 1:00 live demo, 3:00 under the hood with load and chaos, 4:15 impact).
- README quick start, `.env.example`, commits from all members, tag `v1.0-submission`.

### Suggested ownership (4 people)
- A: simulator (Go), ingest, Kafka, load tests, chaos.
- B: data (schema, Timescale, Parquet, batch, SQL optimisation), planner algorithms.
- C: API, auth, security, privacy, Helm/Terraform/CI.
- D: web UI, ML, agent, observability dashboards, solution document and video.

---

## 16. Deliverables checklist (PDF §13 → where it lives)

| Deliverable | Location |
|---|---|
| Solution Document | docs/solution-document.docx → PDF |
| Repo with README, one-command run, seeded 100K dataset | root, `make run`, `make seed` |
| Architecture diagram, ER (3NF), 3–5 ADRs | docs/architecture, docs/adr |
| Working end-to-end demo on simulated stream | compose stack |
| Test evidence: coverage, load, security, CI link | tests/reports, GitHub Actions |
| DevOps pack: Dockerfiles, Helm, Terraform, STRIDE | services/*/Dockerfile, infra/, docs/threat-model.md |
| Algorithms and SQL write-up | docs/algorithms.md, docs/sql-optimisation.md |
| Demo video ≤ 5 min | link in doc |
| Final tag | v1.0-submission |

---

## 17. Risks and mitigations

| Risk | Mitigation |
|---|---|
| 100K ev/s not reachable on laptops | Go simulator + Redpanda can hit it in-memory; persist 1-in-10 in dev, document the down-sampling; run the real number on a cloud VM once |
| Scope creep across rubric rows | MoSCoW table is the contract; cut Could then Should |
| Agent demo flakiness | Golden questions cached; fall back to deterministic tool output in video |
| Terraform cost | Use `terraform plan` output as evidence if no cloud credit; kind cluster proves Helm |
| Team members' commits uneven | Rubric checks commits from all members; enforce PR per owner |
