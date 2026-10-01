#!/usr/bin/env python3
"""Run the agent golden questions and report pass rate + hallucination checks.

  # in-process against create_app() wired to the in-memory fakes (no Docker, no network):
  cd services && uv run python ../tests/agent_eval/run_eval.py
  # against a running API (dev token minted via /auth/dev-token, admin role so /audit is readable):
  cd services && uv run python ../tests/agent_eval/run_eval.py --base-url http://localhost:8000

The in-process run uses offline mode unless --llm is given *and* ANTHROPIC_API_KEY is set (real API calls).
Hallucination check: every number in an answer must appear in some tool result of that run
(AGENT_TOOL.details.result_numbers in the audit log) or in the question. Dates, times and VINs are ignored.
"""

import argparse
import asyncio
import os
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
API_TESTS = REPO / "services" / "api" / "tests"
PREFIX = "/api/v1"

_ISO_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?")
_VINLIKE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_NUMBER = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")


def number_tokens(text: str) -> list[str]:
    return [t.replace(",", "") for t in _NUMBER.findall(_VINLIKE.sub(" ", _ISO_TS.sub(" ", text)))]


def ungrounded(answer: str, grounded: set[float]) -> list[str]:
    bad = []
    for tok in number_tokens(answer):
        val = float(tok)
        dp = len(tok.split(".")[1]) if "." in tok else 0
        tol = 0.5 * 10 ** (-dp) + 1e-9
        if not any(abs(abs(g) - abs(val)) <= tol for g in grounded):
            bad.append(tok)
    return bad


async def run_one(client: httpx.AsyncClient, headers: dict[str, str], item: dict[str, Any]) -> dict[str, Any]:
    q, exp = item["question"], item.get("expect", {})
    r = await client.post(f"{PREFIX}/agent/query", headers=headers, json={"question": q})
    if r.status_code != 200:
        return {"id": item["id"], "ok": False, "failures": [f"HTTP {r.status_code}: {r.text[:200]}"]}
    body = r.json()
    tools, grounded = await tool_rows(client, headers, body["audit_id"])
    grounded |= {float(t) for t in number_tokens(q)}
    answer = body["answer"]
    failures: list[str] = []
    if exp.get("tools_any") and not set(exp["tools_any"]) & set(tools):
        failures.append(f"expected one of {exp['tools_any']}, called {tools}")
    if exp.get("tools_all") and not set(exp["tools_all"]) <= set(tools):
        failures.append(f"expected all of {exp['tools_all']}, called {tools}")
    if exp.get("no_tools") and tools:
        failures.append(f"expected no tools, called {tools}")
    if exp.get("numbers") and not number_tokens(answer):
        failures.append("expected numbers in the answer")
    if "action" in exp and bool(body["actions"]) != bool(exp["action"]):
        failures.append(f"expected action={exp['action']}, got {len(body['actions'])} action(s)")
    for s in exp.get("contains", []):
        if s.lower() not in answer.lower():
            failures.append(f"answer lacks '{s}'")
    halluc = ungrounded(answer, grounded)
    if halluc:
        failures.append(f"ungrounded numbers {halluc}")
    return {
        "id": item["id"],
        "ok": not failures,
        "failures": failures,
        "hallucinated": bool(halluc),
        "tools": tools,
        "mode": body.get("mode"),
        "usage": body.get("usage", {}),
        "answer": answer,
    }


async def tool_rows(client: httpx.AsyncClient, headers: dict[str, str], audit_id: int) -> tuple[list[str], set[float]]:
    """Find this run's AGENT_TOOL rows via GET /audit (admin), keyed by the AGENT_QUERY row's actor id."""
    rows: list[dict[str, Any]] = []
    cursor = None
    for _ in range(20):
        url = f"{PREFIX}/audit?limit=200" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(url, headers=headers)).json()
        rows.extend(page["items"])
        if any(r["id"] == audit_id for r in rows) or not page.get("next_cursor"):
            break
        cursor = page["next_cursor"]
    actor = next((r["actor_id"] for r in rows if r["id"] == audit_id), None)
    mine = sorted((r for r in rows if r["actor_id"] == actor and r["action"] == "AGENT_TOOL"), key=lambda r: r["id"])
    grounded: set[float] = set()
    for r in mine:
        grounded.update(float(x) for x in r["details"].get("result_numbers", []))
    return [r["details"]["name"] for r in mine], grounded


async def run_http(base_url: str, items: list[dict[str, Any]], tenant: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        tok = await client.post(f"{PREFIX}/auth/dev-token", json={"tenant_id": tenant, "role": "admin", "sub": "agent-eval"})
        tok.raise_for_status()
        headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
        return [await run_one(client, headers, it) for it in items]


async def run_in_process(items: list[dict[str, Any]], use_llm: bool) -> list[dict[str, Any]]:
    os.environ.setdefault("ORBIT_ENV", "dev")
    os.environ.setdefault("JWT_DEV_SECRET", "agent-eval-secret-agent-eval-secret")
    os.environ.setdefault("JWKS_URL", "")
    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    sys.path.insert(0, str(API_TESTS))
    import fakeredis.aioredis
    from api_agent_support import enrich, seed_redis
    from api_fakes import FakePublisher, fake_repos, seed
    from orbit_api.agent import AgentConfig, load_config
    from orbit_api.audit import AuditEntry, AuditQueue
    from fastapi import Request
    from orbit_api.auth import CurrentPrincipal, mint_dev_token
    from orbit_api.deps import get_repos
    from orbit_api.main import Resources, create_app
    from orbit_api.routers.agent import get_agent_config

    data = enrich(seed())
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await seed_redis(redis, data)

    async def sink(batch: list[AuditEntry]) -> None:
        return None

    app = create_app(
        Resources(redis=redis, publisher=FakePublisher(), audit=AuditQueue(sink), preset=True)
    )

    async def override(request: Request, principal: CurrentPrincipal) -> AsyncIterator[Any]:
        yield fake_repos(data, principal.tenant_id)  # tenant comes from the verified principal

    app.dependency_overrides[get_repos] = override
    cfg = load_config() if use_llm else AgentConfig()
    app.dependency_overrides[get_agent_config] = lambda: cfg
    headers = {"Authorization": f"Bearer {mint_dev_token(1, 'admin', 'agent-eval')}"}
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://eval") as client:
            return [await run_one(client, headers, it) for it in items]


def report(results: list[dict[str, Any]], verbose: bool) -> float:
    passed = sum(r["ok"] for r in results)
    halluc = sum(r.get("hallucinated", False) for r in results)
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"{mark}  {r['id']:<22} mode={r.get('mode')} tools={','.join(r.get('tools', [])) or '-'}")
        for f in r["failures"]:
            print(f"      - {f}")
        if verbose and r.get("answer"):
            print("      > " + r["answer"].replace("\n", "\n        "))
    n = len(results)
    lat = [r["usage"].get("latency_ms", 0) for r in results if r.get("usage")]
    cost = sum(r["usage"].get("cost_usd", 0.0) for r in results if r.get("usage"))
    rate = passed / n if n else 0.0
    print(f"\npass rate: {passed}/{n} = {rate:.0%}")
    print(f"hallucination check: {n - halluc}/{n} answers fully grounded ({halluc} with ungrounded numbers)")
    if lat:
        print(f"latency ms: mean {sum(lat) / len(lat):.1f}, max {max(lat)}; total LLM cost ${cost:.4f}")
    return rate


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", help="Running API base URL, e.g. http://localhost:8000 (default: in-process)")
    ap.add_argument("--golden", default=str(HERE / "golden.yaml"))
    ap.add_argument("--tenant", type=int, default=1)
    ap.add_argument("--llm", action="store_true", help="in-process: use LLM mode if ANTHROPIC_API_KEY is set")
    ap.add_argument("--min-pass", type=float, default=0.9)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    items = yaml.safe_load(Path(args.golden).read_text())["questions"]
    if args.base_url:
        results = asyncio.run(run_http(args.base_url.rstrip("/"), items, args.tenant))
    else:
        results = asyncio.run(run_in_process(items, args.llm))
    return 0 if report(results, args.verbose) >= args.min_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
