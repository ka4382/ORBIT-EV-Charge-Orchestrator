from __future__ import annotations

import importlib

import pytest
from api_auth import auth
from orbit_api.ratelimit import RateLimiter


async def test_token_bucket_refills():
    redis = importlib.import_module("fakeredis.aioredis").FakeRedis(decode_responses=True)
    limiter = RateLimiter(redis, capacity=3, window_s=60)
    results = [await limiter.take("alice") for _ in range(4)]
    assert [r[0] for r in results] == [True, True, True, False]
    assert results[-1][2] >= 1  # retry-after seconds
    assert await redis.ttl("ratelimit:alice") > 0
    # another subject has its own bucket
    assert (await limiter.take("bob"))[0] is True


async def test_429_with_retry_after(app, client):
    app.state.rate_limiter = RateLimiter(app.state.redis, capacity=2)
    h = auth(sub="burst")
    assert (await client.get("/api/v1/me", headers=h)).status_code == 200
    assert (await client.get("/api/v1/me", headers=h)).status_code == 200
    r = await client.get("/api/v1/me", headers=h)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 429 and body["type"] == "urn:orbit:problem:rate-limited"
    assert body["retry_after"] >= 1
    # unauthenticated routes are not limited
    assert (await client.get("/healthz")).status_code == 200


@pytest.mark.parametrize("n", [1, 5])
async def test_limit_is_per_subject(app, client, n):
    app.state.rate_limiter = RateLimiter(app.state.redis, capacity=n)
    for i in range(n):
        assert (await client.get("/api/v1/me", headers=auth(sub=f"s{i}"))).status_code == 200
