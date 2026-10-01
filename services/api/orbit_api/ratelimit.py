"""Redis token bucket: 100 requests / minute per JWT subject. Key `ratelimit:{sub}` (STRING, 60 s TTL)."""

from __future__ import annotations

import time

from redis.asyncio import Redis

from .problem import ProblemException

DEFAULT_CAPACITY = 100
WINDOW_S = 60

# Bucket state is "tokens:last_ms". Atomic refill + take in one round trip.
_BUCKET_SCRIPT = """
local capacity = tonumber(ARGV[1])
local refill_per_ms = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl_ms = tonumber(ARGV[4])
local tokens = capacity
local last = now
local data = redis.call('GET', KEYS[1])
if data then
  local sep = string.find(data, ':', 1, true)
  tokens = tonumber(string.sub(data, 1, sep - 1))
  last = tonumber(string.sub(data, sep + 1))
end
if now > last then tokens = math.min(capacity, tokens + (now - last) * refill_per_ms) end
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('SET', KEYS[1], string.format('%.6f', tokens) .. ':' .. tostring(now), 'PX', ttl_ms)
local retry_ms = 0
if allowed == 0 then retry_ms = math.ceil((1 - tokens) / refill_per_ms) end
return {allowed, math.floor(tokens), retry_ms}
"""


class RateLimiter:
    def __init__(self, redis: Redis, capacity: int = DEFAULT_CAPACITY, window_s: int = WINDOW_S) -> None:
        self._redis = redis
        self.capacity = capacity
        self.window_s = window_s
        self._refill_per_ms = capacity / (window_s * 1000.0)
        self._script = redis.register_script(_BUCKET_SCRIPT)

    async def take(self, sub: str) -> tuple[bool, int, int]:
        """Return (allowed, remaining_tokens, retry_after_seconds)."""
        now_ms = int(time.time() * 1000)
        allowed, remaining, retry_ms = await self._script(
            keys=[f"ratelimit:{sub}"], args=[self.capacity, self._refill_per_ms, now_ms, self.window_s * 1000]
        )
        return (
            bool(int(allowed)),
            int(remaining),
            max(1, -(-int(retry_ms) // 1000)) if not int(allowed) else 0,
        )

    async def check(self, sub: str) -> None:
        allowed, remaining, retry_after = await self.take(sub)
        if not allowed:
            raise ProblemException(
                429,
                f"rate limit of {self.capacity} requests per {self.window_s}s exceeded",
                type_="urn:orbit:problem:rate-limited",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.capacity),
                    "X-RateLimit-Remaining": "0",
                },
                retry_after=retry_after,
            )
