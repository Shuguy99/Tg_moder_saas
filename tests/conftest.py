from types import SimpleNamespace

import pytest
from aiogram.types import User
from fakeredis import FakeAsyncRedis

from bot.config import get_settings
from bot.support.ratelimit import RateLimiter


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:TEST")
    monkeypatch.setenv("MODE", "polling")
    monkeypatch.setenv("SUPERADMIN_IDS", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_redis() -> FakeAsyncRedis:
    return FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def fake_limiter(fake_redis: FakeAsyncRedis) -> RateLimiter:
    return RateLimiter(fake_redis)


class RecordingBot:
    def __init__(self, *, me_id: int = 1000, username: str = "mod_bot") -> None:
        self.me_user = User(id=me_id, is_bot=True, first_name="mod_bot", username=username)
        self.calls: list[tuple[str, tuple, dict]] = []
        self.member_count_by_chat: dict[int, int] = {}
        self.member_status_by_user: dict[int, str] = {}

    async def me(self) -> User:
        return self.me_user

    def __getattr__(self, name: str):
        async def caller(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "get_chat":
                return SimpleNamespace(member_count=self.member_count_by_chat.get(args[0], 0))
            if name == "get_chat_member":
                return SimpleNamespace(status=self.member_status_by_user.get(args[1], "member"))
            if name == "answer_callback_query":
                return True
            return None

        return caller

    def called(self, name: str) -> int:
        return sum(1 for n, _, _ in self.calls if n == name)
