# ORBIT fleet agent

`POST /api/v1/agent/query` answers fleet managers' questions ("which vehicles should charge tonight and
what does it save?", "status of VIN X", "open critical alerts") from live data, and can **propose** a
charging re-plan that a human approves. Code: `services/api/orbit_api/agent/`.

## Design

```
POST /agent/query ──► AgentService.query()                    (manager | admin only)
                        │  AGENT_QUERY audit row (id = response.audit_id)
                        │
                        ├─ strategy: LlmAgent      (ANTHROPIC_API_KEY set)
                        │            OfflineAgent  (no key, or LLM failure → fallback)
                        │
                        └─ ToolExecutor  ── allowlist per role, ≤ 5 calls, arg validation,
                             │               VIN check digit, untrusted-data wrapping,
                             │               AGENT_TOOL audit row per call
                             └─ tools over tenant-scoped repos (RLS) + Redis state store
                        │  AGENT_ANSWER audit row (usage, cost, latency)
                        ▼
              {answer, actions, audit_id, mode, usage}

POST /agent/actions/{id}/approve ──► decide APPROVED ──► REPLAN? ──► request_plan() per VIN
                                      (charging_plan PENDING + plans.requested, same code as POST /plans)
                                      ──► action EXECUTED | FAILED + AGENT_EXECUTED audit row
```

- **Strategy pattern.** `LlmAgent` and `OfflineAgent` share one interface
  (`run(question, executor) -> RunResult`) and the same `ToolExecutor`, so guardrails, audit and the
  returned `actions` are identical in both modes.
- **The agent never writes plans.** Its only write is `propose_replan`, which inserts an `agent_action`
  row with status `PENDING`. Execution happens only in the existing approve endpoint, under the
  approver's identity, through `orbit_api/plan_requests.request_plan` — the function `POST /plans` now
  uses too, so both paths create identical rows, events and `PLAN_REQUESTED` audit entries.
- **LLM loop.** A manual tool-use loop over `client.beta.messages.create` (Anthropic Python SDK). We
  use a manual loop rather than the SDK tool runner because every turn has to pass through server-side
  checks: the tool-call cap, a cost cap that also bounds `max_tokens`, an audit row per call, and the
  wrapping of results as untrusted data. History is append-only (assistant content is echoed back
  unchanged), and all `tool_result` blocks of a turn go back in a single user message.

## Tools

All tools take the tenant and role from the verified JWT (`ToolContext.principal` and repositories already
scoped with `SET LOCAL app.tenant_id`). No tool has a tenant or role parameter, and unknown arguments are
rejected (`extra="forbid"`, `additionalProperties: false`).

| Tool | Args | Returns | Roles |
|---|---|---|---|
| `get_fleet_summary` | none | vehicles, online, charging, at_risk, avg_soc, kwh_today, open_alerts, cost_today, savings_today | admin, manager, finance, auditor |
| `list_at_risk_vehicles` | `limit` 1–50 (10) | vehicles with status AT_RISK or SoC < 30 %, lowest SoC first, kWh to 80 %, plus `charge_tonight` (kWh needed, off-peak vs peak cost, est. savings from the tenant tariff) | admin, manager |
| `get_vehicle_state` | `vin` | model, live state (SoC, SoH, status, charging, DTC, position at 3 dp, masked for finance/auditor), est. range | admin, manager |
| `nearest_chargers` | `vin`, `limit` 1–10 (3) | chargers near the live position (or depot) with distance, kW, price, reachable; other tenants' private chargers are excluded | admin, manager |
| `get_plan` | `plan_id` | plan with slots, cost, baseline, savings | admin, manager, finance |
| `cost_report` | `days` 1–90 (7) | per-day kWh/cost/baseline/savings + totals and savings % | admin, manager, finance |
| `list_open_alerts` | `severity` INFO/WARN/CRITICAL (optional), `limit` 1–50 (10) | open alerts; the free-form payload is returned as an opaque `payload_json` value | admin, manager, auditor |
| `propose_replan` (write) | `vins` (1–50), `target_soc` 20–100, `depart_ts` (15 min – 7 days ahead), `rationale` | `{action_id, status: PENDING, ...}`; never creates plans | admin, manager |

Repository additions (read-only except where noted): `VehicleRepository.find_by_vins` (ownership check:
`WHERE tenant_id = $1 AND vin = ANY($2)`, under RLS), `AgentRepository.propose` (insert PENDING
action) and `AgentRepository.set_status` (APPROVED → EXECUTED/FAILED).

## Guardrails

| Threat / risk | Control (where) |
|---|---|
| Cross-tenant access, "replan tenant 2" | Tenant and role only from the JWT; repos are RLS-scoped; `propose_replan` checks every VIN with `find_by_vins` in SQL and rejects the whole proposal if any VIN is unknown or foreign (`tools.py`) |
| Privilege escalation | `/agent/query` and approve/reject require manager/admin; per-role tool allowlist `ROLE_TOOLS` is enforced again in `ToolExecutor` (finance/auditor cannot call `propose_replan` even if a model asks) |
| Prompt injection via telemetry/alerts (STRIDE #5) | Tool output is sanitised (strings truncated to 300 chars, lists to 50 items, control characters stripped), instruction-like text is flagged, and each result is sent as one JSON document `{"tool", "untrusted_data": true, "data", "warning"?}`. The system prompt says tool output is untrusted data and that actions are never proposed because a tool result asks. Offline routing reads only the question. Injection-looking questions are refused offline and flagged (`question_flagged`) in the audit row |
| Runaway loops / spend | ≤ 5 tool calls per query (`AGENT_MAX_TOOL_CALLS`, hard max 5; after the cap, the next call is sent with `tool_choice: none`); ≤ tool calls + 2 LLM calls; per-request cost cap `AGENT_COST_CAP_USD` (default $0.10) that also lowers `max_tokens` so a single call cannot exceed it; `AGENT_MAX_TOKENS` per call (4096) |
| Oversized input | Schema cap of 2,000 chars, agent cap `AGENT_MAX_QUESTION_CHARS` (1,000) → 422 problem+json |
| Bad identifiers | VINs validated with `orbit_common.vin.is_valid` (format + ISO 3779 check digit) before any query |
| Unapproved changes | Only PENDING `agent_action` rows are written; plans are created by the approve endpoint, publishing failure → 503 and rollback (the action stays PENDING) |
| Hallucinated numbers | Offline templates print only tool-result values; the system prompt requires every number to come from a tool; the eval checks it (below) |

## Audit trail

Each query writes, in the request transaction, `audit_log` rows with `actor_type='agent'` and
`actor_id = <run id>` (`agent-run-<16 hex>`):

| action | details |
|---|---|
| `AGENT_QUERY` (its id is the response `audit_id`) | question, model (LLM mode), mode, user sub, role, question_flagged |
| `AGENT_TOOL` (one per call, including rejected ones) | name, args, ok, error, result_bytes, latency_ms, call_index, injection_flags, result_numbers (used for groundedness checks) |
| `AGENT_ANSWER` | mode, usage (tokens, cost_usd, cost_cap_usd, latency_ms, fallback_reason, stopped_reason), tool names, action_ids, answer (≤ 4,000 chars) |

Approving writes `AGENT_APPROVED`, one `PLAN_REQUESTED` per plan (with `agent_action_id`) and
`AGENT_EXECUTED` / `AGENT_FAILED` (plan_ids, errors), all as the approving user. The HTTP audit
middleware also logs every `POST`.

## API contract additions

`POST /agent/query` keeps `{answer, actions:[{id, action, params, rationale, status}], audit_id}` and adds:

- `mode`: `"llm"` or `"offline"`: the strategy that produced the answer.
- `usage`: `{model, llm_calls, tool_calls, input_tokens, output_tokens, cache_read_input_tokens,
  cache_creation_input_tokens, cost_usd, cost_cap_usd, latency_ms, fallback_reason, stopped_reason}`.
  `fallback_reason` is set when LLM mode failed and the offline agent answered: `refusal`,
  `rate_limited`, `connection_error`, `authentication_error`, `bad_request`, `api_error_<status>` or
  `client_error:<Type>`. `stopped_reason` is `tool_call_cap`, `cost_cap`, `llm_call_cap` or `max_tokens`.

`POST /agent/actions/{id}/approve` keeps `{id, status: "APPROVED"}` and, when it executed a REPLAN, adds
`execution: {status: "EXECUTED" | "FAILED", plan_ids: [...], errors: [...]}`. It returns 503 if the event
bus is down, and nothing is committed in that case. Reject is unchanged.

## Modes

**Offline (default, no key).** `OfflineAgent` routes the question with regexes (pure function
`offline.route`) and calls the same tools:

| Intent | Example | Tools |
|---|---|---|
| tonight | "which vehicles should charge tonight / what does it save" | `list_at_risk_vehicles` |
| risk | "which vehicles are at risk" | `list_at_risk_vehicles` |
| cost | "how much did we save this week" (days parsed: today/yesterday/week/month/N days) | `cost_report` |
| vehicle | "status of VIN X" | `get_vehicle_state` |
| nearest | "nearest charger for VIN X" | `nearest_chargers` |
| alerts | "open critical alerts" | `list_open_alerts` |
| plan | "plan 5001" | `get_plan` |
| replan | "schedule / re-plan charging for at-risk vehicles" | `list_at_risk_vehicles` + `propose_replan` (80 %, next 07:00 IST) |
| summary / help / refuse | overview, anything else, injection-like text | `get_fleet_summary` / none / none |

**LLM.** Selected when `ANTHROPIC_API_KEY` is set. Model from `AGENT_MODEL` (default
`claude-sonnet-5-5`, matching `.env.example`). Request shape: adaptive thinking (default on this model,
so no `thinking` parameter), `output_config.effort` = `AGENT_EFFORT` (default `medium`), top-level
`cache_control` for the static system+tools prefix, and server-side refusal fallback
(`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`, Claude API only; turn off with
`AGENT_SERVER_FALLBACK=0` on Bedrock/Vertex/Foundry). A `refusal` stop reason, any API/connection error
or a client error makes the service answer offline and set `usage.fallback_reason`.

### Enabling LLM mode

```bash
# .env (compose) or the API container environment
ANTHROPIC_API_KEY=sk-ant-...
AGENT_MODEL=claude-sonnet-5-5       # or claude-opus-5-5 for harder questions (2x price)
# optional
AGENT_EFFORT=medium                 # low | medium | high
AGENT_COST_CAP_USD=0.10
AGENT_MAX_TOKENS=4096
AGENT_MAX_TOOL_CALLS=5              # 1..5
AGENT_MAX_QUESTION_CHARS=1000
AGENT_SERVER_FALLBACK=1
```

The config is read on every request, so no code change is needed. The API image must be rebuilt once,
because the new `anthropic` dependency is not in the running containers.

## Evaluation

`tests/agent_eval/golden.yaml` has 20 questions. Each one lists the expected tool(s), whether numbers
must appear, whether an action is expected (only q14 asks for one), and any required substrings.
`tests/agent_eval/run_eval.py` runs them through the HTTP API and reads the run's `AGENT_TOOL` rows from
`GET /audit` to check which tools were called. It also runs a **hallucination check**: every number in
an answer must appear in that run's tool results (`result_numbers`) or in the question. Dates, times
and VINs are not counted as numbers.

```bash
cd services
uv run python ../tests/agent_eval/run_eval.py                 # in-process: create_app() + fakes
uv run python ../tests/agent_eval/run_eval.py --base-url http://localhost:8000   # live stack (admin dev token)
```

Results (in-process, offline mode, 2026-10-01): **20/20 pass (100 %)**, 20/20 answers fully grounded
(0 hallucinated numbers), mean latency 0.1 ms per query on in-memory fakes. The same run is a unit test
(`services/api/tests/test_api_agent_eval.py`). The golden values are tuned to the fixture data, so some
data-specific checks (for example plan 5001) are expected to fail against live data. LLM-mode eval needs
a key: `run_eval.py --llm`.

## Cost and latency per request (estimates)

| Mode | LLM calls | Tokens | Cost | Latency |
|---|---|---|---|---|
| Offline | 0 | 0 | $0 | 1 tool round trip: ~5–30 ms on Postgres/Redis (0.1 ms on fakes) |
| LLM, typical (1 tool) | 2 | ~4.5–5k input (system + tools ≈ 1.3k), ~400–600 output incl. thinking | ≈ $0.014–0.016 on Sonnet 5.5 ($2 / $10 per MTok) | ~3–8 s (two model calls + tool) |
| LLM, worst case | ≤ 7 | bounded by the cap | ≤ `AGENT_COST_CAP_USD` ($0.10) | ≤ ~30 s |

The token counts are estimates (characters / 4); with a key, use `usage` in the response or
`AGENT_ANSWER.details.usage` for real values. Prompt caching only applies when the system + tools prefix
is at least the model's minimum cacheable length. If it is shorter, it is not cached and no error is
raised. Check `usage.cache_read_input_tokens` across repeated queries.

## Tests

`services/api/tests/test_api_agent_offline.py`, `test_api_agent_replan.py`, `test_api_agent_llm.py`
(fake client implementing `beta.messages.create`; no network) and `test_api_agent_eval.py`. Package
coverage is about 98 %.
