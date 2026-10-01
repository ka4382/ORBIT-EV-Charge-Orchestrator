# ADR-002: PostgreSQL + TimescaleDB + pgvector as the CP store, instead of Cassandra + Qdrant

**Status:** Accepted · 2026-10-01

## Context
The platform needs (a) a 3NF relational core with ACID for fleets, plans, billing, audit; (b) a time-series
store for ~8.6 TB/day of telemetry with range queries and roll-ups; (c) vector similarity for battery
degradation profiles. The obvious "polyglot" answer is three engines.

## Options
1. Postgres (core) + Cassandra (telemetry) + Qdrant (vectors): best raw write scalability, three ops surfaces,
   three failure modes, no joins between telemetry and vehicles.
2. Postgres + TimescaleDB + pgvector: one engine, hypertables give time/space partitioning, compression
   (~10x) and retention policies; pgvector gives ANN search co-located with the vehicle row.
3. ClickHouse for telemetry: superb analytics, weaker for point lookups and no ACID core.

## Decision
Option 2 for the hot path (30 days), with cold telemetry in Parquet on object storage (DuckDB/Spark for
batch). Telemetry is space-partitioned by `hash(vin)` into 16 partitions and time-chunked hourly. The
telemetry table is deliberately denormalised (stores `vin`, not `vehicle_id`) to avoid a join on the write path.

## Consequences
+ One backup/restore story, one connection pool, SQL joins across core and telemetry.
+ Compression and retention are declarative policies.
− Single-writer ceiling. Mitigation: archiver batches with COPY, `ARCHIVER_SAMPLE_DIVISOR` for laptops, and
  a documented path to multi-node Timescale or a Cassandra adapter behind the repository interface.
− pgvector ANN quality is lower than a dedicated vector DB at very large scale; acceptable at 100K vehicles.
