import time

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage

from bot.support.ratelimit import RateLimiter
from bot.support.sessions import FloodGuardMiddleware, create_session

BOT = object()
METHOD_A = SendMessage(chat_id=-100, text="a")
METHOD_B = SendMessage(chat_id=-200, text="b")


class Prompt:
    def __init__(self, retries: int = 0) -> None:
        self.retries = retries
        self.attempts = 0

    async def __call__(self, bot, method):
        self.attempts += 1
        if self.attempts <= self.retries:
            raise TelegramRetryAfter(method=method, message="flood", retry_after=0)
        return "ok"


async def test_global_throttle_waits(fake_limiter: RateLimiter):
    mw = FloodGuardMiddleware(fake_limiter, global_limit=1, global_window=0.3)
    await mw(Prompt(), BOT, METHOD_A)
    start = time.monotonic()
    await mw(Prompt(), BOT, METHOD_B)
    assert time.monotonic() - start >= 0.2


async def test_per_chat_throttle_isolates_chats(fake_limiter: RateLimiter):
    mw = FloodGuardMiddleware(fake_limiter, chat_min_limit=1, chat_min_window=10.0)
    await mw(Prompt(), BOT, METHOD_A)
    await mw(Prompt(), BOT, METHOD_B)
    assert await fake_limiter.acquire("rl:chat:-100", 1, 10.0) > 0.0
    assert await fake_limiter.acquire("rl:chat:-300", 1, 10.0) == 0.0


async def test_retry_after_sleeps_and_resends(fake_limiter: RateLimiter):
    mw = FloodGuardMiddleware(fake_limiter)
    prompt = Prompt(retries=1)
    assert await mw(prompt, BOT, METHOD_A) == "ok"
    assert prompt.attempts == 2


async def test_create_session_returns_aiohttp_session(fake_limiter: RateLimiter):
    assert isinstance(create_session(fake_limiter), AiohttpSession)
