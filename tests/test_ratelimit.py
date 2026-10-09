import asyncio
import time

from bot.support.ratelimit import RateLimiter, throttle


async def test_acquire_allows_within_limit(fake_limiter: RateLimiter):
    assert await fake_limiter.acquire("k", 2, 10.0) == 0.0
    assert await fake_limiter.acquire("k", 2, 10.0) == 0.0


async def test_acquire_throttles_over_limit(fake_limiter: RateLimiter):
    assert await fake_limiter.acquire("k", 1, 0.3) == 0.0
    delay = await fake_limiter.acquire("k", 1, 0.3)
    assert delay > 0.0


async def test_acquire_window_slides(fake_limiter: RateLimiter):
    assert await fake_limiter.acquire("k", 1, 0.2) == 0.0
    assert await fake_limiter.acquire("k", 1, 0.2) > 0.0
    await asyncio.sleep(0.25)
    assert await fake_limiter.acquire("k", 1, 0.2) == 0.0


async def test_acquire_keys_are_independent(fake_limiter: RateLimiter):
    await fake_limiter.acquire("a", 1, 10.0)
    assert await fake_limiter.acquire("b", 1, 10.0) == 0.0


async def test_throttle_waits_when_over_budget(fake_limiter: RateLimiter):
    await fake_limiter.acquire("k", 1, 0.3)
    start = time.monotonic()
    await throttle(fake_limiter, key="k", limit=1, window=0.3)
    assert time.monotonic() - start >= 0.25
