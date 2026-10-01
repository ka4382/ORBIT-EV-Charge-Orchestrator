# ADR-005: Python asyncio consumers now, Flink later

**Status:** Accepted · 2026-10-01

## Context
Rule evaluation, state projection and archiving are per-event or per-vehicle operations with small windows
(2 h SoC ring buffer). Flink or Kafka Streams offer exactly-once and stateful windows at the cost of a JVM
cluster, checkpoint storage and a second language.

## Decision
Each stream concern is a small Python service built on aiokafka with batch consumption (`getmany`), manual
offset commits after side effects, and horizontal scaling through consumer groups (two ingest replicas in
compose). Windowed state lives in memory keyed by VIN and is mirrored to Redis so a restarted pod recovers.
The Go simulator, not Python, carries the 100K msg/s producer load.

## Consequences
+ One language for services, tests with plain pytest, easy to reason about back-pressure (pause/resume).
+ Each service is stateless from the platform's point of view; Kubernetes can kill and reschedule freely.
− At-least-once, not exactly-once: duplicates are handled downstream (see ADR-003).
− Python throughput per replica is ~20–40K events/s; 100K/s needs 3–5 replicas per group. When windows grow
  (driver scoring over days), migrate the alert service to Flink behind the same topic contracts.
