from __future__ import annotations

import asyncio
import time
import uuid

from redis.asyncio import Redis


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def acquire(self, key: str, limit: int, window: float) -> float:
        now = time.time()
        member = f"{now}:{uuid.uuid4().hex[:8]}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - window)
            pipe.zadd(key, {member: now})
            pipe.zcard(key)
            pipe.zrange(key, 0, 0, withscores=True)
            _, _, count, oldest = await pipe.execute()
        if count <= limit:
            return 0.0
        if oldest:
            return max(0.0, (oldest[0][1] + window) - now)
        return 0.0


async def throttle(limiter: RateLimiter, *, key: str, limit: int, window: float) -> None:
    delay = await limiter.acquire(key, limit, window)
    if delay > 0:
        await asyncio.sleep(delay)
