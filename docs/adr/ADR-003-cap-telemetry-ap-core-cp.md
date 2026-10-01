# ADR-003: Telemetry path is AP / at-least-once; plans, billing and audit are CP

**Status:** Accepted · 2026-10-01

## Context
CAP forces a choice under partition. A 3x burst or a broker restart must never lose or block telemetry, yet
a charging plan or an audit row must never be half-written or visible to the wrong tenant.

## Decision
| Data | Choice | Mechanism |
|---|---|---|
| Raw and canonical telemetry | **AP**, at-least-once | Kafka acks=all, idempotent producer, consumers dedupe (Bloom) and tolerate replays (unique index, seq compare) |
| Live vehicle state, geo index, summaries | **AP** cache | Redis with TTL; rebuildable by replaying `telemetry.canonical` |
| Fleet, vehicle, plan, slot, session, subscription, audit, agent_action | **CP** | Postgres transactions; RLS by tenant; planner writes plan + slots in one transaction |

PACELC: on the telemetry path we choose latency over consistency (EL) and availability under partition (PA).
On the core we choose consistency in both cases (EC/PC).

## Consequences
+ Bursts and broker failures degrade to lag, never loss. Chaos test `tests/chaos/kill_broker.sh` proves it.
+ Money and ownership never see torn writes.
− A dashboard can be up to a few seconds stale relative to the database; the UI shows "last seen" timestamps.
− Alerts may fire twice after a replay; suppressed by the 15-minute Redis dedupe key.
