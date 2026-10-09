from __future__ import annotations

import asyncio
from typing import Any

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramRetryAfter

from bot.support.ratelimit import RateLimiter


class FloodGuardMiddleware(BaseRequestMiddleware):
    def __init__(
        self,
        limiter: RateLimiter,
        *,
        global_limit: int = 30,
        global_window: float = 1.0,
        chat_min_limit: int = 20,
        chat_min_window: float = 60.0,
        chat_sec_limit: int = 1,
        chat_sec_window: float = 1.0,
    ) -> None:
        self.limiter = limiter
        self.global_limit = global_limit
        self.global_window = global_window
        self.chat_min_limit = chat_min_limit
        self.chat_min_window = chat_min_window
        self.chat_sec_limit = chat_sec_limit
        self.chat_sec_window = chat_sec_window

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType,
        bot: Any,
        method: Any,
    ) -> Any:
        chat_id = getattr(method, "chat_id", None)
        delays = [await self.limiter.acquire("rl:global", self.global_limit, self.global_window)]
        if chat_id is not None:
            delays.append(
                await self.limiter.acquire(
                    f"rl:chat:{chat_id}", self.chat_min_limit, self.chat_min_window
                )
            )
            delays.append(
                await self.limiter.acquire(
                    f"rl:chat:{chat_id}:sec", self.chat_sec_limit, self.chat_sec_window
                )
            )
        delay = max(delays)
        if delay > 0:
            await asyncio.sleep(delay)
        while True:
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)


def create_session(limiter: RateLimiter) -> AiohttpSession:
    session = AiohttpSession()
    session.middleware(FloodGuardMiddleware(limiter))
    return session
