# ADR-001: Kafka-compatible streaming (Redpanda in dev, any Kafka in prod) over RabbitMQ

**Status:** Accepted · 2026-10-01

## Context
100,000 vehicles × 1 event/s with 3x bursts must be ingested durably, replayed for rebuilds (Redis state is a
cache), and consumed independently by four services (state, alert, archiver, ML). Per-vehicle ordering matters
for out-of-order detection.

## Options
1. RabbitMQ — mature, but queues are consumed destructively; replay needs extra queues, and per-key ordering
   across multiple consumers is not native.
2. Apache Kafka — partitioned log, replay by offset, consumer groups, per-key ordering. Operationally heavy
   (ZooKeeper/KRaft, JVM).
3. Redpanda — Kafka API compatible, single binary, no JVM, built-in schema registry. Same client libraries.

## Decision
Program to the Kafka protocol. Run Redpanda in docker compose and CI; the Helm/Terraform packs point at a
managed Kafka (MSK / Confluent / Redpanda Cloud) in production with no code change. Topic contract lives in
`contracts/topics.yaml`; key = VIN so one vehicle always lands on one partition.

## Consequences
+ Replayable: any service can be rebuilt from `telemetry.canonical`.
+ Independent consumer groups; adding a consumer is a config change.
− At-least-once delivery: every consumer must be idempotent (Bloom dedupe in ingest, unique index in
  Timescale, seq comparison in state).
− Partition count (48) fixes max parallelism per group; changing it later re-shuffles keys.
