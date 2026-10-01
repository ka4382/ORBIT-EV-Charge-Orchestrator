# ORBIT — EV Charge Orchestrator

> Every vehicle charged, on time, at the lowest tariff.

ORBIT ingests telemetry from **100,000 simulated connected vehicles at 100K events/s**, keeps a live picture
of every vehicle, predicts range and battery health, computes a **cost-optimal charging schedule under
time-of-use tariffs** with dynamic programming, routes low-charge vehicles to the **nearest reachable charger**
with Dijkstra, raises stranding and battery alerts in under 5 seconds, and lets a guard-railed AI agent
recommend actions that a manager approves. Built for the Connected Vehicle Intelligence Hackathon
(Motorq case study). All data is synthetic.

![architecture](docs/architecture/container.png)

> **Windows?** See [WINDOWS.md](WINDOWS.md): install Docker Desktop, then `.\orbit.cmd demo`.

## Quick start (one command)

```bash
cp .env.example .env
make run            # docker compose --profile sim up --build -d
```

| Surface | URL |
|---|---|
| Web console | http://localhost:3000 (login: any tenant 1–8, role manager) |
| API + OpenAPI | http://localhost:8000/docs |
| Redpanda console | http://localhost:8090 |
| EMQX dashboard | http://localhost:18083 (admin / orbit-admin) |
| MinIO console | http://localhost:9001 (orbit / orbit-secret) |
| Metrics | each service on :9100/metrics, simulator :9101 |

`make seed` creates 8 tenants, 24 depots, 320 public + 288 depot chargers, tariffs and 100,000 vehicles.

**Laptop profile (default in `.env.example` comments, used for the demo):** `SIM_VEHICLES=20000` (20K events/s,
60K/s bursts), Kafka retention capped at 30 min / 100 MB per partition, archiver sampling 1-in-20 to Postgres
and 1-in-10 to Parquet, and `make dev-retention` for 6 h of Timescale history. The full 100K/s profile is a
cluster deployment; see [docs/benchmarks.md](docs/benchmarks.md) for measured per-replica throughput and sizing.

ML (range and battery-health models, pgvector embeddings): `docker compose --profile ml run --rm ml`.
Agent: works offline out of the box; set `ANTHROPIC_API_KEY` to switch it to Claude tool-use mode.

## Repository layout

```
contracts/        Avro schema, topic map, OEM formats, REST/WS and Redis contracts
db/               SQL migrations (3NF core, hypertable, RLS) and runner
simulator/        Go: 100K-vehicle telemetry generator (Kafka or MQTT)
services/         Python uv workspace: common, ingest, state, alert, archiver, planner, ml, api (incl. agent)
web/              React + Vite console (design system in docs/DESIGN.md)
infra/            Helm chart, Terraform (AWS + GCP stub), Grafana dashboards
tests/            integration, acceptance (behave), load (k6), chaos, security
docs/             PLAN, DESIGN, ADRs, threat model, algorithms, SQL optimisation, benchmarks, ml, agent, screenshots
```

## Development

```bash
make infra            # brokers and stores only
make migrate seed     # schema + 100K vehicles
make py-install       # uv workspace
make test             # unit tests: python (coverage), go, web
make test-integration # Testcontainers: real Redpanda, Timescale, Redis
make lint             # ruff, mypy, go vet, eslint
make load             # k6 against the running stack
make chaos            # kill the broker mid-stream, prove recovery
```

## Environment variables

See [.env.example](.env.example). Every service is 12-factor: configuration only from the environment.

## Known issues

- 100K events/s is proven for the producer and broker on one laptop; end to end it needs ~19 cores for the
  Python stream tier (per-replica numbers in docs/benchmarks.md). The laptop demo runs 20K events/s live.
- ML labels are synthetic and follow the simulator's physics, so model metrics show recovery of the
  simulator, not real-world accuracy (docs/ml.md §9).
- The agent's LLM mode is tested with a fake client only; no API key was available during development.
- The MQTT path (EMQX → bridge) is lower throughput than direct Kafka and is meant for the device-identity demo.
- Docker Desktop needs ~20 GB free on the host; with the laptop guards the stack stays within that.

## Licence and declarations

MIT. Open-source components and AI tools used are declared in the Solution Document and `docs/sbom.json`.
