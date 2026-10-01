"""API latency under concurrent load (k6 alternative that needs only Python).

Each virtual user has its own dev token (the API rate-limits 100 req/min per subject), cycles through the
dashboard endpoints and stays under its own limit. Reports p50/p95/p99 per endpoint and overall.
    cd services && uv run python ../tests/load/api_latency.py --users 300 --seconds 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time

import httpx

ENDPOINTS = ["/fleet/summary", "/vehicles?limit=50", "/alerts?limit=50", "/vehicles/{vin}/state",
             "/vehicles/{vin}/nearest-chargers?limit=5", "/vehicles/{vin}/range-forecast"]


async def user(client: httpx.AsyncClient, base: str, uid: int, deadline: float, out: dict[str, list[float]],
               codes: dict[int, int], vins: list[str]) -> None:
    tok = (await client.post(f"{base}/auth/dev-token",
                             json={"tenant_id": 1 + uid % 2, "role": "manager", "sub": f"load-{uid}"})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    await asyncio.sleep(random.random())
    while time.perf_counter() < deadline:
        ep = random.choice(ENDPOINTS)
        path = ep.replace("{vin}", random.choice(vins[uid % 2]))
        t0 = time.perf_counter()
        r = await client.get(base + path, headers=h)
        dt = (time.perf_counter() - t0) * 1000
        codes[r.status_code] = codes.get(r.status_code, 0) + 1
        if r.status_code == 200:
            out.setdefault(ep, []).append(dt)
        await asyncio.sleep(0.65)  # ~90 req/min per user: under the 100/min limit


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000/api/v1")
    ap.add_argument("--users", type=int, default=300)
    ap.add_argument("--seconds", type=int, default=60)
    a = ap.parse_args()
    limits = httpx.Limits(max_connections=a.users, max_keepalive_connections=a.users)
    async with httpx.AsyncClient(timeout=10, limits=limits) as client:
        vins = []
        for t in (1, 2):
            tok = (await client.post(f"{a.base}/auth/dev-token", json={"tenant_id": t, "role": "manager",
                                                                         "sub": f"seed-{t}"})).json()["access_token"]
            r = await client.get(f"{a.base}/vehicles?limit=200", headers={"Authorization": f"Bearer {tok}"})
            vins.append([v["vin"] for v in r.json()["items"]])
        out: dict[str, list[float]] = {}
        codes: dict[int, int] = {}
        start = time.perf_counter()
        await asyncio.gather(*(user(client, a.base, i, start + a.seconds, out, codes, vins) for i in range(a.users)))
        elapsed = time.perf_counter() - start
    allv = [x for v in out.values() for x in v]
    rows = {ep: {"n": len(v), "p50": round(statistics.median(v), 1), "p95": round(pct(v, .95), 1),
                 "p99": round(pct(v, .99), 1)} for ep, v in sorted(out.items())}
    summary = {"users": a.users, "seconds": round(elapsed, 1), "requests": sum(codes.values()),
               "rps": round(sum(codes.values()) / elapsed, 1), "status_codes": codes,
               "overall": {"p50": round(statistics.median(allv), 1), "p95": round(pct(allv, .95), 1),
                           "p99": round(pct(allv, .99), 1)}, "endpoints": rows}
    print(json.dumps(summary, indent=1))
    with open("../tests/reports/api_latency.json", "w") as f:
        json.dump(summary, f, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
