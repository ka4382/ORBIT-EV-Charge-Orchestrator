"""LLM mode with a fake Anthropic client (no network): tool-use loop, untrusted-data message construction,
caps, fallback to offline, config and pricing."""

from __future__ import annotations

import json

import anthropic
import pytest
from api_agent_support import (
    INJECTION,
    FakeAnthropic,
    block,
    prepare_agent_app,
    response,
    use_config,
)
from api_auth import auth
from api_fakes import VIN_T1
from orbit_api.agent import AgentConfig, load_config
from orbit_api.agent.config import DEFAULT_MODEL, estimate_cost_usd
from orbit_api.agent.guard import numbers_in_text, ungrounded_numbers, wrap_result
from orbit_api.agent.llm import SERVER_FALLBACK_BETA, LlmAgent, make_client
from orbit_api.agent.prompts import SYSTEM_PROMPT

URL = "/api/v1/agent/query"


@pytest.fixture
async def agent_app(app, data, redis):
    return await prepare_agent_app(app, data, redis)


def llm_cfg(script, **kw) -> tuple[AgentConfig, FakeAnthropic]:
    fake = FakeAnthropic(script)
    return AgentConfig(client=fake, **kw), fake


def tool_use(name: str, args: dict, tid: str = "toolu_1"):
    return block("tool_use", id=tid, name=name, input=args)


async def test_llm_tool_use_then_end_turn(agent_app, client, data):
    cfg, fake = llm_cfg(
        [
            response(
                [block("thinking", thinking=""), tool_use("list_open_alerts", {"severity": "CRITICAL"})],
                "tool_use",
                i=2000,
                o=150,
            ),
            response(
                [block("text", text="There are 3 open critical alerts.")], "end_turn", i=2600, o=40, cr=1800
            ),
        ]
    )
    use_config(agent_app, cfg)
    r = await client.post(URL, headers=auth(), json={"question": "Any critical alerts?"})
    body = r.json()
    assert r.status_code == 200 and body["mode"] == "llm" and body["actions"] == []
    assert body["answer"] == "There are 3 open critical alerts."
    u = body["usage"]
    assert u["model"] == DEFAULT_MODEL and u["llm_calls"] == 2 and u["tool_calls"] == 1
    assert u["input_tokens"] == 4600 and u["output_tokens"] == 190 and u["cache_read_input_tokens"] == 1800
    assert u["cost_usd"] == pytest.approx((4600 * 2 + 190 * 10 + 1800 * 0.2) / 1e6)
    assert u["fallback_reason"] is None

    first, second = fake.requests
    assert first["model"] == DEFAULT_MODEL and first["system"] == SYSTEM_PROMPT
    assert first["betas"] == [SERVER_FALLBACK_BETA] and first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "medium"} and "tool_choice" not in first
    assert "thinking" not in first and "temperature" not in first
    assert {t["name"] for t in first["tools"]} >= {"list_open_alerts", "propose_replan"}
    assert "<question>\nAny critical alerts?\n</question>" in first["messages"][0]["content"]
    # second call: assistant content echoed unchanged, then one user turn with the tool_result
    assert (
        second["messages"][1]["role"] == "assistant"
        and second["messages"][1]["content"][0].type == "thinking"
    )
    tr = second["messages"][2]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "toolu_1" and "is_error" not in tr

    # prompt injection inside an alert payload is passed as data: one JSON document, flagged, untrusted
    doc = json.loads(tr["content"])
    assert doc["untrusted_data"] is True and doc["tool"] == "list_open_alerts"
    payloads = [a["payload_json"] for a in doc["data"]["items"]]
    assert {"soc": 12, "note": INJECTION} in payloads
    assert "do not follow it" in doc["warning"] and "payload_json.note" in doc["warning"]
    assert "untrusted data" in SYSTEM_PROMPT and "not instructions" in SYSTEM_PROMPT

    rows = [a for a in data.audit if a["actor_type"] == "agent"]
    assert [a["action"] for a in rows] == ["AGENT_QUERY", "AGENT_TOOL", "AGENT_ANSWER"]
    assert rows[0]["details"]["model"] == DEFAULT_MODEL and rows[0]["details"]["mode"] == "llm"
    assert rows[2]["details"]["usage"]["input_tokens"] == 4600


async def test_llm_propose_replan_and_error_results(agent_app, client, data):
    from datetime import UTC, datetime, timedelta

    dep = (datetime.now(UTC) + timedelta(hours=10)).isoformat()
    cfg, fake = llm_cfg(
        [
            response(
                [
                    tool_use(
                        "propose_replan",
                        {"vins": [VIN_T1[0]], "target_soc": 85, "depart_ts": dep, "rationale": "low SoC"},
                        "t1",
                    ),
                    tool_use("get_vehicle_state", {"vin": "NOTAVIN"}, "t2"),
                ],
                "tool_use",
            ),
            response([block("text", text="Proposed a re-plan; it needs approval.")], "end_turn"),
        ]
    )
    use_config(agent_app, cfg)
    body = (
        await client.post(
            URL, headers=auth(), json={"question": "Schedule charging for VIN 1HGCM82633A004352"}
        )
    ).json()
    assert len(body["actions"]) == 1 and body["actions"][0]["status"] == "PENDING"
    assert body["actions"][0]["params"]["target_soc"] == 85
    results = fake.requests[1]["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]  # all results in one user message
    assert results[1]["is_error"] is True and "not a valid VIN" in results[1]["content"]


async def test_llm_tool_cap_forces_final_answer(agent_app, client, data):
    many = [tool_use("get_fleet_summary", {}, f"t{i}") for i in range(7)]
    cfg, fake = llm_cfg(
        [response(many, "tool_use"), response([block("text", text="Summary done.")], "end_turn")]
    )
    use_config(agent_app, cfg)
    body = (await client.post(URL, headers=auth(), json={"question": "overview"})).json()
    assert body["usage"]["tool_calls"] == 5 and body["usage"]["stopped_reason"] == "tool_call_cap"
    results = fake.requests[1]["messages"][2]["content"]
    assert len(results) == 7 and sum(1 for r in results if r.get("is_error")) == 2
    assert fake.requests[1]["tool_choice"] == {"type": "none"}  # no more tool calls allowed
    tool_rows = [a for a in data.audit if a["action"] == "AGENT_TOOL"]
    assert len(tool_rows) == 7


async def test_llm_cost_cap(agent_app, client):
    cfg, fake = llm_cfg(
        [response([tool_use("get_fleet_summary", {})], "tool_use", i=4000, o=4000)],
        cost_cap_usd=0.05,
    )
    use_config(agent_app, cfg)
    body = (await client.post(URL, headers=auth(), json={"question": "overview"})).json()
    assert body["usage"]["stopped_reason"] == "cost_cap" and len(fake.requests) == 1
    assert body["usage"]["cost_usd"] <= 0.05 and "cost limit" in body["answer"]
    assert fake.requests[0]["max_tokens"] <= 4096


async def test_llm_call_cap_and_max_tokens(agent_app, client):
    loop = [response([tool_use("get_fleet_summary", {}, f"t{i}")], "tool_use", i=10, o=10) for i in range(5)]
    cfg, fake = llm_cfg(
        [*loop, response([block("text", text="partial")], "max_tokens", i=10, o=10)], max_tool_calls=5
    )
    use_config(agent_app, cfg)
    body = (await client.post(URL, headers=auth(), json={"question": "overview"})).json()
    assert body["answer"] == "partial" and body["usage"]["stopped_reason"] == "max_tokens"
    assert fake.requests[-1]["tool_choice"] == {"type": "none"}

    cfg, fake = llm_cfg(loop[:1] * 8, max_tool_calls=1, server_fallback=False)
    use_config(agent_app, cfg)
    body = (await client.post(URL, headers=auth(), json={"question": "overview"})).json()
    assert body["usage"]["stopped_reason"] == "llm_call_cap" and "step limit" in body["answer"]
    assert "betas" not in fake.requests[0] and "fallbacks" not in fake.requests[0]


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (RuntimeError("boom"), "client_error:RuntimeError"),
        ("refusal", "refusal"),
        ("conn", "connection_error"),
        ("status", "api_error_529"),
        ("rate", "rate_limited"),
    ],
)
async def test_llm_failure_falls_back_to_offline(agent_app, client, data, failure, reason):
    import httpx2

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    if failure == "refusal":
        item = response([], "refusal")
    elif failure == "conn":
        item = anthropic.APIConnectionError(request=req)
    elif failure == "status":
        item = anthropic.InternalServerError(
            "overloaded", response=httpx2.Response(529, request=req), body=None
        )
    elif failure == "rate":
        item = anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=req), body=None)
    else:
        item = failure
    cfg, _ = llm_cfg([item])
    use_config(agent_app, cfg)
    body = (
        await client.post(URL, headers=auth(), json={"question": "How much did we save this week?"})
    ).json()
    assert body["mode"] == "offline" and body["usage"]["fallback_reason"] == reason
    assert body["usage"]["model"] == DEFAULT_MODEL and "₹420.00" in body["answer"]
    ans = next(a for a in data.audit if a["action"] == "AGENT_ANSWER")
    assert ans["details"]["mode"] == "offline" and ans["details"]["usage"]["fallback_reason"] == reason


async def test_llm_auth_and_bad_request_errors():
    import httpx2
    from orbit_api.agent.llm import LlmUnavailableError
    from orbit_api.agent.models import LlmUsage

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    for exc, reason in (
        (
            anthropic.AuthenticationError("no", response=httpx2.Response(401, request=req), body=None),
            "authentication_error",
        ),
        (
            anthropic.BadRequestError("bad", response=httpx2.Response(400, request=req), body=None),
            "bad_request",
        ),
    ):
        agent = LlmAgent(AgentConfig(client=FakeAnthropic([exc])))
        with pytest.raises(LlmUnavailableError) as info:
            await agent._create({}, LlmUsage())
        assert info.value.reason == reason


def test_config_and_pricing(monkeypatch):
    for k in (
        "ANTHROPIC_API_KEY",
        "AGENT_MODEL",
        "AGENT_MAX_TOOL_CALLS",
        "AGENT_COST_CAP_USD",
        "AGENT_EFFORT",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    assert cfg.model == "claude-sonnet-5-5" and not cfg.llm_enabled and cfg.max_tool_calls == 5
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AGENT_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "50")  # never above the hard cap of 5
    monkeypatch.setenv("AGENT_COST_CAP_USD", "oops")
    cfg = load_config()
    assert cfg.llm_enabled and cfg.model == "claude-opus-5-5" and cfg.max_tool_calls == 5
    assert cfg.cost_cap_usd == 0.10 and cfg.prices() == (4.0, 20.0, 0.2)
    assert AgentConfig(model="claude-unknown").prices()[0] == 5.0
    assert estimate_cost_usd(AgentConfig(), input_tokens=1_000_000, output_tokens=0) == 2.0
    assert isinstance(make_client(cfg), anthropic.AsyncAnthropic)
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "x")
    assert load_config().max_tool_calls == 5


async def test_offline_selected_without_key(agent_app, client, monkeypatch):
    from orbit_api.routers.agent import get_agent_config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent_app.dependency_overrides.pop(get_agent_config)
    body = (await client.post(URL, headers=auth(), json={"question": "fleet overview"})).json()
    assert body["mode"] == "offline" and body["usage"]["model"] is None


def test_guard_helpers():
    text, flags = wrap_result("t", {"a": "x" * 1000, "b": [1] * 80})
    doc = json.loads(text)
    assert len(doc["data"]["a"]) == 300 and len(doc["data"]["b"]) == 50 and flags == []
    text, _ = wrap_result("t", {"rows": ["y" * 290] * 50, "more": ["z" * 290] * 50})
    assert json.loads(text)["truncated"] is True
    assert numbers_in_text(f"{VIN_T1[0]} at 2026-10-01T07:00:00+05:30 has 1,234.5 kWh and 12%") == [
        1234.5,
        12.0,
    ]
    assert ungrounded_numbers("SoC 12.0% and 99 km", {12.04}) == [99.0]
