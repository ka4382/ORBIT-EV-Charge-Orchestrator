# STRIDE threat model — ingestion path and public API

Scope: vehicle → MQTT/Kafka → ingest → stores → API → browser, plus the AI agent. Assets: telemetry (location
is personal data under GDPR/DPDP), tenant isolation, plans (money), audit integrity, LLM tool access.

## Top five threats and controls

| # | Threat | STRIDE | Likelihood / Impact | Control (where) |
|---|---|---|---|---|
| 1 | A spoofed device publishes fake SoC or location for another VIN | Spoofing | Med / High | mTLS per device on EMQX with VIN bound to the certificate CN; ingest rejects events whose VIN ≠ topic VIN (`fleet/{tenant}/{vin}`); VIN check digit + Avro validation |
| 2 | Replayed or duplicated events inflate mileage or trigger repeated alerts | Tampering | High / Med | Per-vehicle `seq`; rolling Bloom dedupe in ingest; unique `(vin, seq, ts)` index in Timescale; seq-monotonic writes in state; ts skew > 5 min rejected |
| 3 | A user of tenant A reads tenant B's vehicles or alerts | Information disclosure | Med / High | `tenant_id` comes only from the verified JWT; API sets `SET LOCAL app.tenant_id` and Postgres RLS policies enforce it on every table; Redis keys are tenant-prefixed; integration test proves isolation |
| 4 | API flood or 3x burst starves dashboards | Denial of service | High / Med | Redis token bucket (100 req/min/subject); ingest pause/resume back-pressure; Kafka buffers bursts; HPA on stateless pods; circuit breaker around Postgres in the API |
| 5 | Prompt injection through telemetry text makes the agent replan the wrong fleet or leak data | Elevation of privilege | Med / High | Agent tools are an allowlist; tenant scope is injected server-side, never from the prompt; write tools create `agent_action` rows that need human approval; every prompt, tool call and response is written to `audit_log`; max 5 tool calls and a cost cap per request |

## Other threats considered
- **Repudiation:** every write endpoint and every agent action writes an `audit_log` row with actor, action,
  resource and details; audit rows are append-only for `orbit_app` (no UPDATE/DELETE grant in production).
- **Secrets in images or repo:** gitleaks in CI, `.env` ignored, secrets via Docker secrets locally and
  External Secrets Operator (Vault / cloud secret manager) in Kubernetes.
- **Vulnerable dependencies and images:** Trivy on filesystem and images, Semgrep SAST, ZAP baseline DAST,
  non-root distroless/slim runtime images.
- **Transport:** TLS 1.3 at the ingress; SASL/TLS to Kafka in production values; MQTT over 8883.
- **At rest:** encrypted volumes (cloud KMS) for Postgres and object storage SSE; MinIO SSE-S3 locally.

## Privacy (GDPR / DPDP Act 2023)
- Minimisation: driver names stored masked, licence as SHA-256; no free-text personal data in telemetry.
- Masking: roles `finance` and `auditor` receive location rounded to ~1 km.
- Retention: raw telemetry 30 days in Timescale (policy), 90 days warm Parquet, then cold tier; Redis 10 min.
- Erasure: `DELETE /api/v1/privacy/drivers/{id}` cascades assignments, masks the driver row, and records an
  `ERASURE` audit entry; the archiver's Parquet files carry no driver identifiers, so erasure needs no rewrite.
- Every data access by privileged roles is audited (middleware).
