# REST + WebSocket contract (v1)

Base: `/api/v1`. Auth: `Authorization: Bearer <JWT>`. JWT claims: `sub`, `tenant_id` (int), `role` in
{admin, manager, finance, auditor}, `iss`, `exp`. Dev tokens are HS256 signed with JWT_DEV_SECRET and
minted by `POST /api/v1/auth/dev-token` (only when ORBIT_ENV=dev). Prod verifies RS256 via JWKS_URL.

Errors: RFC 7807 `application/problem+json` `{type, title, status, detail, instance, trace_id}`.
Pagination: keyset. Request `?limit=50&cursor=<opaque>`; response `{items: [...], next_cursor: string|null}`.
Rate limit: 100 req/min per subject (Redis token bucket); 429 with `Retry-After`.
Location masking: roles `finance` and `auditor` receive lat/lon rounded to 2 decimals (~1 km).

| Method | Path | Role | Response |
|---|---|---|---|
| GET | /healthz | none | `{status:"ok"}` |
| GET | /readyz | none | `{kafka, postgres, redis}` booleans |
| POST | /auth/dev-token | none (dev) | body `{tenant_id, role, sub?}` → `{access_token, expires_in}` |
| GET | /me | any | `{sub, tenant_id, role}` |
| GET | /fleets | any | `{items:[{id, name, depot_id, vehicle_count}]}` |
| GET | /fleet/summary | any | `{vehicles, online, charging, at_risk, avg_soc, open_alerts, kwh_today, cost_today, savings_today}` |
| GET | /vehicles?fleet_id&status&limit&cursor | any | items: `{id, vin, plate, fleet_id, model, powertrain, state: VehicleState|null}` |
| GET | /vehicles/{vin} | any | vehicle + model + `state` |
| GET | /vehicles/{vin}/state | any | VehicleState (from Redis) |
| GET | /vehicles/{vin}/telemetry?from&to&limit&cursor | any | items: telemetry rows (Timescale), cursor on (ts, seq) |
| GET | /vehicles/{vin}/nearest-chargers?limit=5 | any | `{items:[{charger_id, name, lat, lon, max_kw, distance_km, reachable, price_per_kwh}]}` |
| GET | /vehicles/{vin}/range-forecast | any | `{range_km, method, confidence}` |
| POST | /plans | manager, admin | body `{vin, target_soc, depart_ts}` → 202 `{plan_id, status:"PENDING"}` (publishes plans.requested) |
| GET | /plans/{id} | any | `{id, vin, status, target_soc, depart_ts, total_cost, baseline_cost, savings, achievable_soc, infeasible, slots:[{charger_id, start_ts, end_ts, kw, kwh, cost, price_per_kwh}]}` |
| GET | /plans?vin&status&limit&cursor | any | items as above without slots |
| GET | /alerts?severity&since&acked&limit&cursor | any | items: `{id, vin, type, severity, ts, payload, acked_by, acked_ts}` |
| POST | /alerts/{id}/ack | manager, admin | `{id, acked_by, acked_ts}` |
| GET | /chargers?depot_id&public&limit&cursor | any | items: charger rows |
| GET | /tariffs/current | any | `{tariff_id, name, slots:[{dow, start_min, end_min, price_per_kwh}]}` |
| GET | /reports/cost?from&to | finance, manager, admin | `{days:[{date, kwh, cost, baseline_cost, savings, peak_kw}]}` |
| GET | /reports/battery-health?limit&cursor | any | items: `{vin, soh_pct, cycles, trend_30d, similar:[vin]}` |
| POST | /agent/query | manager, admin | `{question}` (≤ 1000 chars) → `{answer, actions:[{id, action, params, rationale, status}], audit_id, mode, usage}` |
| POST | /agent/actions/{id}/approve | manager, admin | `{id, status:"APPROVED", execution?:{status:"EXECUTED"|"FAILED", plan_ids, errors}}`; 503 if the event bus is down (nothing committed) |
| POST | /agent/actions/{id}/reject | manager, admin | `{id, status:"REJECTED"}` |
| DELETE | /privacy/drivers/{id} | admin | 202 `{job_id}` — erasure cascades and writes audit row |
| GET | /audit?limit&cursor | admin, auditor | audit_log rows |
| WS | /ws/fleet?token=<JWT> | any | server pushes messages below |

## VehicleState (Redis JSON value at `veh:{vin}`, also the WS `state` payload)
```json
{"vin":"...","tenant_id":12,"ts":"2026-09-25T10:15:02.120Z","seq":88412,"lat":21.17,"lon":72.83,
 "speed_kmh":64.2,"soc_pct":41.0,"soh_pct":93.5,"odo_km":18234.7,"charging_state":"NONE","charging_kw":0,
 "dtc":["P0301"],"status":"DRIVING"}
```
`status` ∈ {DRIVING, IDLE, CHARGING, OFFLINE, AT_RISK}. OFFLINE when no event for 120 s.

## WebSocket messages (JSON, one per frame)
- `{"type":"state","data":VehicleState}` — throttled to at most 1 per vehicle per 2 s, only vehicles in the tenant.
- `{"type":"alert","data":Alert}`
- `{"type":"plan","data":{plan_id, vin, status}}`
- `{"type":"summary","data":<fleet summary>}` every 5 s.
Client may send `{"type":"subscribe","fleet_id":3}` to narrow, or `{"type":"subscribe","vins":[...]}`.

## Alert types
`STRAND_RISK` (CRITICAL: soc<15 and no reachable charger within range), `LOW_SOC` (WARN: soc<20),
`SOC_DROP` (WARN: >40 pts in 2 h), `SOH_DROP` (WARN: soh below 80 or −2 pts/30 d), `BATTERY_DTC` (CRITICAL),
`CHARGE_INTERRUPTED` (WARN), `OFF_PLAN` (INFO: charging outside planned slot at peak tariff).

## Agent details

- `mode`: `"llm"` (Claude tool-use loop, when `ANTHROPIC_API_KEY` is set) or `"offline"` (deterministic intent
  router over the same tools). If the LLM call fails the answer comes from offline mode and
  `usage.fallback_reason` says why.
- `usage`: `{model, llm_calls, tool_calls, input_tokens, output_tokens, cache_read_input_tokens,
  cache_creation_input_tokens, cost_usd, cost_cap_usd, latency_ms, fallback_reason, stopped_reason}`.
- Action `status` lifecycle: `PENDING` → `APPROVED` → `EXECUTED` | `FAILED`, or `PENDING` → `REJECTED`.
  The agent can only create `PENDING` actions; approval by a manager is what creates charging plans.
- `GET /audit` rows: `{id, ts, actor_type, actor_id, action, resource, details}`. Agent runs write
  `AGENT_QUERY`, one `AGENT_TOOL` per tool call, and `AGENT_ANSWER`; approval writes `AGENT_APPROVED`,
  `PLAN_REQUESTED` per plan, and `AGENT_EXECUTED` or `AGENT_FAILED`.
