# Moderation Implementation Plan (antispam, greeting, captcha, rate limiting)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Рабочие модули модерации: трёхуровневый outbound rate limiting через кастомную aiogram-сессию, подключение/архивация чатов с abuse-лимитом (10 чатов на модератора), анти-спам (ссылки/КАПС/повторы/стоп-слова с `delete` и `delete+warn`), приветствие с кнопками и брендингом, жёсткая капча с идемпотентным флоу «Я человек».

**Architecture:** Модульный монолит на aiogram 3: один handler-роутер `chatflow` владеет событием `chat_member` и разводит события по модулям; `antispam` владеет текстовыми `message`; `captcha` владеет `callback_query`. Outbound-троттлинг живёт в middleware сессии (`FloodGuardMiddleware`), счётчики — скользящее окно в Redis (`RateLimiter`). Это **План 2 из 3**: План 1 (каркас/БД/миграции/точка входа) выполнен и слит; План 3 добавит worker (APScheduler: таймауты капчи, шедулер, агрегация статистики, renewal), billing, мониторинг и входящий троттлинг.

**Tech Stack:** Python 3.12+, aiogram 3.31, SQLAlchemy 2 async + aiosqlite (юнит) / psycopg3 (интеграция), Redis (`redis.asyncio`), fakeredis (юнит), pytest + pytest-asyncio, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-10-06-community-mod-bot-design.md` — аргументы выводятся из разделов «Анти-спам», «Приветствие», «Капча», «Онбординг чата», «Rate limits / Flood control», «Redis», «Модели БД». Предыдущий план: `docs/superpowers/plans/2026-10-06-foundation.md`.

## Global Constraints

- Python `>=3.12`; aiogram `>=3.24,<4`; тесты юнит — только `aiosqlite` + `fakeredis` (без реальной сети и реального Redis/Postgres); интеграционный тест миграций неизменен.
- Redis хранит только эфемерное состояние (троттлинг, дедуп повторов) — ключи эфемерные, с префиксами `rl:*` и `repeat:*`; долгоживущее — только в PostgreSQL (спека, «Redis»).
- Текст сообщений в `MessageLog` и Redis-дедупе храним только как необратимый `sha256`-хеш (спека: «Текст сообщения не храним… дедупликация через Redis слепок в течение окна»).
- Внешний `chat_id` (BigInteger, Telegram) — для Telegram API и партиционированных `MessageLog`/`ChatMemberEvents`; таблицы `Moderator`, `Member`, `CaptchaChallenge` ссылаются на **`chats.id` (PK)** — не смешивать: FK-поля получают `chat.id` из строки `Chat`, API-вызовы — `chat.chat_id`.
- Jействия на сообщения — строго `delete`/`delete+warn`; бан только после `warn_count >= warn_limit` (спека, «Анти-спам»).
- На `free`-тарифе работают только предустановленные базовые фильтры: стоп-слова и whitelist доменов игнорируются (спека: «На бесплатном тарифе действуют только предустановленные базовые фильтры; гибкие правила — на pro»).
- Анти-спам и капча игнорируют админов/самого бота: проверка через `is_chat_admin` (План 1, `bot/support/permissions.py`).
- Вызовы к Telegram API только через аргументы `bot: Bot` (чтобы тесты записывали вызовы через `RecordingBot`); в хендлерах НЕ используем `message.delete()` / `callback_query.answer()` — только `bot.delete_message(...)` / `bot.answer_callback_query(...)`.
- Если хендлер не сработал — возвращает `False` (пропустить событие дальше), если сработал — `None`.
- Идентификаторы в коде — английские; комментарии в код не добавляем.
- Перед каждым коммитом: `make format && make lint && make typecheck && make test` — всё зелёное.
- Коммиты только файлов внутри `tg_botSaas/` (корень репозитория — `фриланс`; удаления `../tgbot`, `../AGENTS.md` НЕ трогать, `git add` по явным путям).
- Commit message — conventional commits (`feat:`, `test:`, `docs:`, `chore:`).

## Структура файлов (новое/изменённое в Плане 2)

```
tg_botSaas/
  pyproject.toml                        # M: dev-deps += fakeredis
  bot/
    app.py                              # M: create_dispatcher(db, redis), rate_limiter, сессия, роутеры
    support/
      ratelimit.py                      # A: RateLimiter (скользящее окно), throttle()
      sessions.py                       # A: FloodGuardMiddleware, create_session()
      chats.py                          # A: get_or_create_chat, add_moderator, count_active_chats_for_moderator,
                                        #    set_archived, update_member_count, default_settings(), MAX_CHATS_PER_MODERATOR
    modules/
      __init__.py                       # A
      chatflow.py                       # A: Router chat_member -> онбординг/архив/abuse-limit; маршрутизация join
      antispam/
        __init__.py                     # A
        filters.py                      # A: detect_links/caps/stopwords, check_text
        handler.py                      # A: Router message -> delete/warn/ban, repeat via redis, MessageLog, warn_count
      greeting/
        __init__.py                     # A
        handler.py                      # A: on_member_joined (текст+кнопки+брендинг, broadcast-throttle)
      captcha/
        __init__.py                     # A
        handler.py                      # A: on_member_joined (restrict+challenge), callback CAS done+restore
  tests/
    conftest.py                        # M: fake_redis fixture, RecordingBot
    test_ratelimit.py                  # A
    test_sessions.py                   # A
    test_chats.py                      # A
    test_chatflow.py                   # A
    test_antispam_filters.py           # A
    test_antispam.py                   # A
    test_greeting.py                   # A
    test_captcha.py                    # A
```

Итоговые счётчики тестов (шаги задач): unit 24 → **71** (33 → 40 → 44 → 56 → 62 → 66 → 71), integration 1 (неизменно).

---

### Task 1: Rate limiting (Redis-счётчики + aiogram-сессия)

**Files:**
- Modify: `pyproject.toml` (dev-deps: `fakeredis`)
- Create: `bot/support/ratelimit.py`, `bot/support/sessions.py`
- Create: `tests/test_ratelimit.py`, `tests/test_sessions.py`
- Modify: `tests/conftest.py` (фикстура `fake_redis`)

**Interfaces:**
- Consumes: `redis.asyncio.Redis` (из `bot/support/redis.create_redis`); aiohttp-сессия aiogram (`AiohttpSession`).
- Produces:
  - `bot.support.ratelimit.RateLimiter` — `__init__(self, redis: Redis)`; `async acquire(self, key: str, limit: int, window: float) -> float` (0 — можно сразу, иначе секунды ожидания); `bot.support.ratelimit.throttle(limiter, *, key: str, limit: int, window: float) -> None` (уснуть на delay, если не хватает бюджета).
  - `bot.support.sessions.FloodGuardMiddleware(BaseRequestMiddleware)` — параметры конструктора: `(limiter, *, global_limit=30, global_window=1.0, chat_min_limit=20, chat_min_window=60.0, chat_sec_limit=1, chat_sec_window=1.0)`; перехватывает `TelegramRetryAfter` и спит `retry_after`.
  - `bot.support.sessions.create_session(limiter: RateLimiter) -> AiohttpSession`.
  - Константы ключей: глобальный `"rl:global"`, по-чатовые `"rl:chat:{chat_id}"` и `"rl:chat:{chat_id}:sec"`, broadcast `"rl:bcast:{chat_id}"`.

- [ ] **Step 1: Добавить fakeredis в dev-зависимости и переустановить окружение**

В `pyproject.toml` в блок `[project.optional-dependencies].dev` добавить строку:

```toml
    "fakeredis>=2.23",
```

```bash
make install
```

- [ ] **Step 2: Добавить в `tests/conftest.py` фикстуру `fake_redis` и хелпер `RecordingBot`**

В конец файла `tests/conftest.py` (после существующего кода) добавить:

```python
from fakeredis import FakeAsyncRedis

from bot.support.ratelimit import RateLimiter


@pytest.fixture
def fake_redis() -> FakeAsyncRedis:
    return FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def fake_limiter(fake_redis: FakeAsyncRedis) -> RateLimiter:
    return RateLimiter(fake_redis)
```

В конец файла добавить класс `RecordingBot` (перехватывает любые методы бота и записывает вызовы):

```python
from types import SimpleNamespace

from aiogram.types import User


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
```

- [ ] **Step 3: Написать падающий тест `tests/test_ratelimit.py`**

```python
import asyncio
import time

import pytest
from fakeredis import FakeAsyncRedis

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
```

- [ ] **Step 4: Убедиться, что тест падает**

```bash
make test
```

Expected: FAIL — `ModuleNotFoundError: No module named 'bot.support.ratelimit'`.

- [ ] **Step 5: Реализовать `bot/support/ratelimit.py`**

```python
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
```

- [ ] **Step 6: Убедиться, что `tests/test_ratelimit.py` зелёный (падает только `test_sessions`)**

```bash
make test
```

Expected: тесты `test_ratelimit.py` — 5 passed; сбор пытается загрузить `tests/test_sessions.py` — см. Step 7 (создаётся в шаге 8; до этого тест-раннер просто не видит файла).

- [ ] **Step 7: Написать падающий тест `tests/test_sessions.py`**

```python
import asyncio
import time

import pytest
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
    from aiogram.client.session.aiohttp import AiohttpSession

    assert isinstance(create_session(fake_limiter), AiohttpSession)
```

- [ ] **Step 8: Убедиться, что `tests/test_sessions.py` падает (модуль отсутствует)**

Run: `make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.support.sessions'`.

- [ ] **Step 9: Реализовать `bot/support/sessions.py`**

```python
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
```

- [ ] **Step 10: Убедиться, что все 9 новых тестов зелёные**

Run: `make test`
Expected: 33 passed (24 + 9), 1 deselected.

- [ ] **Step 11: Линт, типы, формат, коммит**

```bash
make format && make lint && make typecheck
git add pyproject.toml bot/support/ratelimit.py bot/support/sessions.py tests/conftest.py tests/test_ratelimit.py tests/test_sessions.py
git commit -m "feat: three-level outbound rate limiting with redis-backed session middleware"
```

---

### Task 2: Хранилище чатов и настройки по умолчанию

**Files:**
- Create: `bot/support/chats.py`
- Create: `tests/test_chats.py`

**Interfaces:**
- Consumes: `Chat`, `Moderator` (модели Плана 1); `async_sessionmaker[AsyncSession]` через `Database.session_factory`.
- Produces:
  - `bot.support.chats.MAX_CHATS_PER_MODERATOR = 10` (спека, «Abuse protection: максимум 10 чатов на одного Moderator»).
  - `bot.support.chats.default_settings() -> dict` — структура настроек чата ниже.
  - `async get_or_create_chat(session_factory, external_chat_id: int) -> Chat`
  - `async update_member_count(session_factory, chat_pk: int, member_count: int) -> None`
  - `async add_moderator(session_factory, chat_pk: int, user_id: int) -> None` (идемпотентно, уникальность `uq_moderators_chat_id_user_id`)
  - `async count_active_chats_for_moderator(session_factory, user_id: int) -> int` (неархивные чаты)
  - `async set_archived(session_factory, chat_pk: int, archived: bool) -> None`

Структура `default_settings()` (ключи жёсткие — их читают модули Плана 2):

```python
{
    "antispam": {
        "enabled": True,
        "warn_limit": 3,
        "caps_threshold": 0.7,
        "caps_min_len": 8,
        "repeat_cluster_enabled": True,  # повторное сообщение юзера за окно
        "repeat_window_min": 5,
        "behavior": {
            "link": "delete+warn",
            "caps": "delete",
            "repeat": "delete+warn",
            "stopword": "delete+warn",
        },
        "link_whitelist": [],
        "stopwords": [],
    },
    "greeting": {"enabled": False, "text": "", "buttons": []},
    "captcha": {"enabled": True, "window_min": 10},
}
```

- [ ] **Step 1: Написать падающий тест `tests/test_chats.py`**

```python
from sqlalchemy import func, select

from bot.db.base import Base
from bot.db.engine import create_database
from bot.models import Chat, Moderator
from bot.support.chats import (
    MAX_CHATS_PER_MODERATOR,
    add_moderator,
    count_active_chats_for_moderator,
    default_settings,
    get_or_create_chat,
    set_archived,
    update_member_count,
)


async def test_default_settings_shape():
    cfg = default_settings()
    assert cfg["antispam"]["warn_limit"] == 3
    assert cfg["antispam"]["behavior"]["link"] == "delete+warn"
    assert cfg["greeting"]["enabled"] is False
    assert cfg["captcha"]["enabled"] is True


async def test_get_or_create_chat_creates_with_defaults():
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        chat = await get_or_create_chat(db.session_factory, -100)
        assert chat.chat_id == -100
        assert chat.settings == default_settings()
        assert chat.plan == "free"
        assert chat.show_branding is True
    finally:
        await db.dispose()


async def test_get_or_create_chat_returns_existing():
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        first = await get_or_create_chat(db.session_factory, -100)
        second = await get_or_create_chat(db.session_factory, -100)
        assert first.id == second.id == 1
    finally:
        await db.dispose()


async def test_add_moderator_idempotent():
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        chat = await get_or_create_chat(db.session_factory, -100)
        await add_moderator(db.session_factory, chat.id, 42)
        await add_moderator(db.session_factory, chat.id, 42)
        async with db.session() as session:
            count = (
                await session.execute(select(func.count()).select_from(Moderator))
            ).scalar_one()
        assert count == 1
    finally:
        await db.dispose()


async def test_count_active_chats_for_moderator():
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        c1 = await get_or_create_chat(db.session_factory, -100)
        c2 = await get_or_create_chat(db.session_factory, -200)
        await add_moderator(db.session_factory, c1.id, 42)
        await add_moderator(db.session_factory, c2.id, 42)
        assert await count_active_chats_for_moderator(db.session_factory, 42) == 2
        await set_archived(db.session_factory, c1.id, True)
        assert await count_active_chats_for_moderator(db.session_factory, 42) == 1
        assert await count_active_chats_for_moderator(db.session_factory, 7) == 0
    finally:
        await db.dispose()


async def test_update_member_count():
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        chat = await get_or_create_chat(db.session_factory, -100)
        await update_member_count(db.session_factory, chat.id, 5123)
        async with db.session() as session:
            fresh = (await session.execute(select(Chat).where(Chat.id == chat.id))).scalar_one()
        assert fresh.member_count == 5123
    finally:
        await db.dispose()


async def test_max_chats_per_moderator_constant():
    assert MAX_CHATS_PER_MODERATOR == 10
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.support.chats'`.

- [ ] **Step 3: Реализовать `bot/support/chats.py`**

```python
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.models import Chat, Moderator

MAX_CHATS_PER_MODERATOR = 10


def default_settings() -> dict:
    return {
        "antispam": {
            "enabled": True,
            "warn_limit": 3,
            "caps_threshold": 0.7,
            "caps_min_len": 8,
            "repeat_cluster_enabled": True,
            "repeat_window_min": 5,
            "behavior": {
                "link": "delete+warn",
                "caps": "delete",
                "repeat": "delete+warn",
                "stopword": "delete+warn",
            },
            "link_whitelist": [],
            "stopwords": [],
        },
        "greeting": {"enabled": False, "text": "", "buttons": []},
        "captcha": {"enabled": True, "window_min": 10},
    }


async def get_or_create_chat(
    session_factory: async_sessionmaker[AsyncSession], external_chat_id: int
) -> Chat:
    async with session_factory() as session:
        chat = (
            await session.execute(select(Chat).where(Chat.chat_id == external_chat_id))
        ).scalar_one_or_none()
        if chat is None:
            chat = Chat(chat_id=external_chat_id, settings=default_settings())
            session.add(chat)
            await session.flush()
            await session.commit()
        return chat


async def update_member_count(
    session_factory: async_sessionmaker[AsyncSession], chat_pk: int, member_count: int
) -> None:
    async with session_factory() as session:
        chat = await session.get(Chat, chat_pk)
        if chat is not None:
            chat.member_count = member_count
            await session.commit()


async def add_moderator(
    session_factory: async_sessionmaker[AsyncSession], chat_pk: int, user_id: int
) -> None:
    async with session_factory() as session:
        existing = (
            await session.execute(
                select(Moderator.id).where(
                    Moderator.chat_id == chat_pk, Moderator.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(Moderator(chat_id=chat_pk, user_id=user_id))
            await session.commit()


async def count_active_chats_for_moderator(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> int:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Moderator.user_id)
                .join(Chat, Chat.id == Moderator.chat_id)
                .where(Moderator.user_id == user_id, Chat.archived.is_(False))
            )
        ).all()
        return len(rows)


async def set_archived(
    session_factory: async_sessionmaker[AsyncSession], chat_pk: int, archived: bool
) -> None:
    async with session_factory() as session:
        chat = await session.get(Chat, chat_pk)
        if chat is not None:
            chat.archived = archived
            await session.commit()
```

- [ ] **Step 4: Убедиться, что тесты зелёные**

Run: `make test`
Expected: 40 passed (33 + 7 новых в этом файле), 1 deselected.

- [ ] **Step 5: Линт, типы, формат, коммит**

```bash
make format && make lint && make typecheck
git add bot/support/chats.py tests/test_chats.py
git commit -m "feat: chat store with default settings and moderator abuse limit"
```

---

### Task 3: Онбординг чата (`chatflow`) — добавление/архив/abuse-limit

**Files:**
- Create: `bot/modules/__init__.py`, `bot/modules/chatflow.py`
- Create: `tests/test_chatflow.py`

**Interfaces:**
- Consumes: `get_or_create_chat`, `add_moderator`, `count_active_chats_for_moderator`, `set_archived`, `update_member_count`, `MAX_CHATS_PER_MODERATOR` (Task 2); workflow-функции `data["captcha_on_member_joined"]`, `data["greeting_on_member_joined"]` (регистрируются в `app.py`, Task 8 — чтобы Task 3 не зависел от ещё не созданных модулей); `bot: Bot`; `data["session_factory"]`, `data["rate_limiter"]`.
- Produces: `chatflow.router = Router(name="chatflow")`; `on_chat_member(update, bot, data) -> bool` (единственный handler события `chat_member`; маршрутизирует события бота и участников).

- [ ] **Step 1: Написать падающий тест `tests/test_chatflow.py`**

```python
from datetime import UTC, datetime

from aiogram.types import Chat as TgChat
from aiogram.types import ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, User
from sqlalchemy import select

from bot.db.base import Base
from bot.db.engine import create_database
from bot.models import Chat, Moderator
from bot.modules.chatflow import on_chat_member, router
from conftest import RecordingBot


def make_update(
    *, chat_id: int, user_id: int, is_bot: bool, new_left: bool, old_left: bool, from_id: int
) -> ChatMemberUpdated:
    user = User(id=user_id, is_bot=is_bot, first_name="Bot" if is_bot else "User")
    old = ChatMemberLeft(user=user) if old_left else ChatMemberMember(user=user)
    new = ChatMemberLeft(user=user) if new_left else ChatMemberMember(user=user)
    return ChatMemberUpdated(
        chat=TgChat(id=chat_id, type="supergroup"),
        from_user=User(id=from_id, is_bot=False, first_name="Admin"),
        date=datetime.now(UTC),
        old_chat_member=old,
        new_chat_member=new,
    )


def data_for(db, fake_redis, fake_limiter):
    return {
        "session_factory": db.session_factory,
        "redis": fake_redis,
        "rate_limiter": fake_limiter,
    }


async def test_chatflow_router_exports():
    assert router.name == "chatflow"


async def test_bot_added_creates_chat_and_moderator(fake_redis, fake_limiter):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        bot = RecordingBot(me_id=1000)
        bot.member_count_by_chat[-100] = 777
        update = make_update(
            chat_id=-100, user_id=1000, is_bot=True, new_left=False, old_left=True, from_id=42
        )
        assert await on_chat_member(update, bot, data_for(db, fake_redis, fake_limiter)) is True
        async with db.session() as session:
            chat = (await session.execute(select(Chat))).scalars().all()
            mods = (await session.execute(select(Moderator))).scalars().all()
        assert len(chat) == 1 and chat[0].chat_id == -100 and chat[0].member_count == 777
        assert len(mods) == 1 and mods[0].user_id == 42
    finally:
        await db.dispose()


async def test_bot_added_restores_archived(fake_redis, fake_limiter):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        from bot.support.chats import get_or_create_chat, set_archived

        chat = await get_or_create_chat(db.session_factory, -100)
        await set_archived(db.session_factory, chat.id, True)
        bot = RecordingBot(me_id=1000)
        update = make_update(
            chat_id=-100, user_id=1000, is_bot=True, new_left=False, old_left=True, from_id=42
        )
        await on_chat_member(update, bot, data_for(db, fake_redis, fake_limiter))
        async with db.session() as session:
            fresh = (await session.execute(select(Chat).where(Chat.chat_id == -100))).scalar_one()
        assert fresh.archived is False
    finally:
        await db.dispose()


async def test_bot_added_abuse_limit_leaves_chat(fake_redis, fake_limiter):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        from bot.support.chats import (
            add_moderator,
            count_active_chats_for_moderator,
            get_or_create_chat,
        )

        for ext in range(10):
            chat = await get_or_create_chat(db.session_factory, -100 - ext)
            await add_moderator(db.session_factory, chat.id, 42)
        assert await count_active_chats_for_moderator(db.session_factory, 42) == 10
        bot = RecordingBot(me_id=1000)
        update = make_update(
            chat_id=-555, user_id=1000, is_bot=True, new_left=False, old_left=True, from_id=42
        )
        await on_chat_member(update, bot, data_for(db, fake_redis, fake_limiter))
        assert bot.called("leave_chat") == 1
        assert bot.calls[0][1][0] == -555
    finally:
        await db.dispose()


async def test_bot_removed_archives(fake_redis, fake_limiter):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        from bot.support.chats import get_or_create_chat

        chat = await get_or_create_chat(db.session_factory, -100)
        assert chat.archived is False
        bot = RecordingBot(me_id=1000)
        update = make_update(
            chat_id=-100, user_id=1000, is_bot=True, new_left=True, old_left=False, from_id=42
        )
        await on_chat_member(update, bot, data_for(db, fake_redis, fake_limiter))
        async with db.session() as session:
            fresh = (await session.execute(select(Chat).where(Chat.chat_id == -100))).scalar_one()
        assert fresh.archived is True
    finally:
        await db.dispose()
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.modules.chatflow'`.

- [ ] **Step 3: Реализовать `bot/modules/chatflow.py`** (и `bot/modules/__init__.py` — пустой файл)

`bot/modules/__init__.py` — пустой файл.

```python
from __future__ import annotations

from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.support.chats import (
    MAX_CHATS_PER_MODERATOR,
    add_moderator,
    count_active_chats_for_moderator,
    get_or_create_chat,
    set_archived,
    update_member_count,
)
from bot.support.ratelimit import RateLimiter

router = Router(name="chatflow")

JOIN_STATUSES = (
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.RESTRICTED,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.CREATOR,
)


async def on_chat_member(update: ChatMemberUpdated, bot: Bot, data: dict) -> bool:
    session_factory: async_sessionmaker[AsyncSession] = data["session_factory"]
    limiter: RateLimiter = data["rate_limiter"]
    new = update.new_chat_member
    me = await bot.me()
    if new.user.id == me.id and new.user.is_bot:
        if new.status == ChatMemberStatus.LEFT:
            chat = await get_or_create_chat(session_factory, update.chat.id)
            await set_archived(session_factory, chat.id, True)
        else:
            await _handle_bot_added(session_factory, bot, update)
        return True
    if new.status == ChatMemberStatus.LEFT:
        return True
    if (
        update.old_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)
        and new.status in JOIN_STATUSES
    ):
        chat = await get_or_create_chat(session_factory, update.chat.id)
        user = new.user
        await data["captcha_on_member_joined"](session_factory, bot, limiter, chat, user)
        await data["greeting_on_member_joined"](session_factory, bot, limiter, chat, user)
    return True


async def _handle_bot_added(
    session_factory: async_sessionmaker[AsyncSession], bot: Bot, update: ChatMemberUpdated
) -> None:
    chat = await get_or_create_chat(session_factory, update.chat.id)
    adder_id = update.from_user.id
    if await count_active_chats_for_moderator(session_factory, adder_id) >= MAX_CHATS_PER_MODERATOR:
        await bot.leave_chat(update.chat.id)
        return
    await add_moderator(session_factory, chat.id, adder_id)
    if chat.archived:
        await set_archived(session_factory, chat.id, False)
    try:
        full = await bot.get_chat(update.chat.id)
        member_count = getattr(full, "member_count", 0) or 0
    except Exception:
        member_count = 0
    await update_member_count(session_factory, chat.id, member_count)
```

- [ ] **Step 4: Убедиться, что тесты зелёные**

Run: `make test`
Expected: 44 passed (40 + 4 новых), 1 deselected.

- [ ] **Step 5: Линт, типы, формат, коммит**

```bash
make format && make lint && make typecheck
git add bot/modules/__init__.py bot/modules/chatflow.py tests/test_chatflow.py
git commit -m "feat: chat onboarding with archive and ten-chat abuse limit"
```

---

### Task 4: Анти-спам — чистые фильтры

**Files:**
- Create: `bot/modules/antispam/__init__.py`, `bot/modules/antispam/filters.py`
- Create: `tests/test_antispam_filters.py`

**Interfaces:**
- Consumes: `FilterType`, `Plan` (План 1); структура `settings["antispam"]` (Task 2).
- Produces (все — чистые функции, без I/O):
  - `bot.modules.antispam.filters.detect_links(text: str, whitelist: frozenset[str]) -> bool`
  - `detect_caps(text: str, *, threshold: float = 0.7, min_len: int = 8) -> bool`
  - `detect_stopwords(text: str, stopwords: frozenset[str]) -> bool`
  - `check_text(text: str, cfg: dict, plan: str) -> FilterType | None` — порядок: stopword → link → caps; на `free` стоп-слова и whitelist игнорируются.

- [ ] **Step 1: Написать падающий тест `tests/test_antispam_filters.py`**

```python
from bot.models import FilterType, Plan
from bot.modules.antispam.filters import (
    detect_caps,
    detect_links,
    detect_stopwords,
    check_text,
)

CFG = {
    "link_whitelist": ["example.com"],
    "stopwords": ["free money", "spam"],
    "caps_threshold": 0.7,
    "caps_min_len": 8,
}


def test_detect_links_finds_plain_url():
    assert detect_links("смотри http://evilsite.net/abc", frozenset()) is True


def test_detect_links_ignores_whitelisted():
    assert detect_links("www.example.com/hello", frozenset({"example.com"})) is False


def test_detect_links_finds_tme():
    assert detect_links("подпишись t.me/anybody", frozenset()) is True


def test_detect_caps_positive():
    assert detect_caps("СРОЧНАЯ ПРОДАЖА ТОКЕНОВ") is True


def test_detect_caps_short_negative():
    assert detect_caps("ОК КУПЛЮ") is False


def test_detect_caps_mostly_lower_negative():
    assert detect_caps("Покупка и Продажа токенов для друзей") is False


def test_detect_stopwords_positive_and_negative():
    assert detect_stopwords("получи free money сейчас", frozenset({"free money"})) is True
    assert detect_stopwords("нормальное сообщение", frozenset({"free money"})) is False


def test_check_text_stopword_first():
    assert check_text("free money http://evilsite.net", CFG, Plan.PRO.value) == FilterType.STOPWORD


def test_check_text_link_when_no_stopwords():
    assert check_text("http://evilsite.net", CFG, Plan.PRO.value) == FilterType.LINK


def test_check_text_whitelisted_link_not_fired():
    assert check_text("www.example.com", CFG, Plan.PRO.value) is None


def test_check_text_caps():
    assert check_text("АКЦИЯ ВСЕМ РАЗДАТЬ ТОКЕНЫ БЕСПЛАТНО", CFG, Plan.PRO.value) == FilterType.CAPS


def test_check_text_free_ignores_stopwords_and_whitelist():
    assert check_text("получи free money", CFG, Plan.FREE.value) is None
    assert check_text("www.example.com", CFG, Plan.FREE.value) == FilterType.LINK
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.modules.antispam.filters'`.

- [ ] **Step 3: Реализовать фильтры**

`bot/modules/antispam/__init__.py` — пустой файл.

```python
from __future__ import annotations

import re

from bot.models import FilterType, Plan

LINK_RE = re.compile(r"(?:https?://|www\.|t\.me/|telegram\.me/)\S+", re.IGNORECASE)
LINK_DOMAIN_RE = re.compile(r"(?:https?://)?(?:www\.)?([^/\s:@]+)", re.IGNORECASE | re.UNICODE)


def detect_links(text: str, whitelist: frozenset[str]) -> bool:
    for match in LINK_RE.findall(text):
        m = LINK_DOMAIN_RE.search(match)
        domain = m.group(1).lower() if m else ""
        if domain and domain not in whitelist:
            return True
        if not domain:
            return True
    return False


def detect_caps(text: str, *, threshold: float = 0.7, min_len: int = 8) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters or len(letters) < min_len:
        return False
    upper = sum(ch.isupper() for ch in letters)
    return upper / len(letters) > threshold


def detect_stopwords(text: str, stopwords: frozenset[str]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in stopwords)


def check_text(text: str, cfg: dict, plan: str) -> FilterType | None:
    if plan == Plan.PRO.value:
        whitelist = frozenset(cfg.get("link_whitelist", []))
        stopwords = frozenset(cfg.get("stopwords", []))
    else:
        whitelist = frozenset()
        stopwords = frozenset()
    if stopwords and detect_stopwords(text, stopwords):
        return FilterType.STOPWORD
    if detect_links(text, whitelist):
        return FilterType.LINK
    if detect_caps(
        text, threshold=cfg.get("caps_threshold", 0.7), min_len=cfg.get("caps_min_len", 8)
    ):
        return FilterType.CAPS
    return None
```

- [ ] **Step 4: Убедиться, что тесты зелёные**

Run: `make test`
Expected: 56 passed (44 + 12 новых), 1 deselected.

- [ ] **Step 5: Линт, типы, коммит**

```bash
make format && make lint && make typecheck
git add bot/modules/antispam/__init__.py bot/modules/antispam/filters.py tests/test_antispam_filters.py
git commit -m "feat: pure antispam filters for links, caps and stopwords"
```

---

### Task 5: Анти-спам — handler с delete/warn/ban, повторной дедупликацией и MessageLog

**Files:**
- Create: `bot/modules/antispam/handler.py`
- Create: `tests/test_antispam.py`

**Interfaces:**
- Consumes: `check_text` (Task 4); `is_chat_admin` (`bot/support/permissions.py`); `Chat`, `Member`, `MessageLog`, `FilterType`, `LogAction` (План 1); `data["session_factory"]`, `data["redis"]`.
- Produces:
  - `antispam.router = Router(name="antispam")`; `@router.message(F.chat.type.in_({"group", "supergroup"}), F.text)`
  - `async warn_member(session_factory, chat_pk: int, user_id: int) -> int` (вернёт новый `warn_count`)
  - `async log_action(session_factory, *, chat_id: int, user_id: int, filter_type: str, action: str) -> None`
  - `on_message(message, bot, data) -> bool | None` (False — не спам; None — обработано).

- [ ] **Step 1: Написать падающий тест `tests/test_antispam.py`**

```python
from datetime import UTC, datetime

from aiogram.types import Chat, Message, User
from sqlalchemy import select

from bot.db.base import Base
from bot.db.engine import create_database
from bot.models import Member, MessageLog
from bot.modules.antispam.handler import on_message
from bot.support.chats import default_settings, get_or_create_chat
from conftest import RecordingBot


def make_message(*, chat_id: int = -100, user_id: int = 42, text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="supergroup"),
        from_user=User(id=user_id, is_bot=False, first_name="User"),
        text=text,
    )


def data_for(db, fake_redis):
    return {"session_factory": db.session_factory, "redis": fake_redis}


async def setup_chat(db, chat_id: int = -100) -> None:
    chat = await get_or_create_chat(db.session_factory, chat_id)
    async with db.session() as session:
        db_chat = await session.get(type(chat), chat.id)
        db_chat.settings = default_settings()
        db_chat.plan = "pro"
        await session.commit()


async def test_link_deletes_and_logs(fake_redis):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await setup_chat(db)
        bot = RecordingBot()
        msg = make_message(text="купить http://evilsite.net")
        assert await on_message(msg, bot, data_for(db, fake_redis)) is None
        assert bot.called("delete_message") == 1
        async with db.session() as session:
            rows = (await session.execute(select(MessageLog))).scalars().all()
        assert len(rows) == 2
        assert rows[0].filter_type == "link" and rows[0].action == "delete"
        assert rows[1].filter_type == "link" and rows[1].action == "warn"
    finally:
        await db.dispose()


async def test_link_warns_and_increments_warn_count(fake_redis):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await setup_chat(db)
        bot = RecordingBot()
        await on_message(make_message(text="привет http://spam.net"), bot, data_for(db, fake_redis))
        await on_message(make_message(text="снова http://spam.net"), bot, data_for(db, fake_redis))
        assert bot.called("ban_chat_member") == 0
        async with db.session() as session:
            member = (await session.execute(select(Member))).scalar_one()
        assert member.warn_count == 2
    finally:
        await db.dispose()


async def test_ban_after_warn_limit(fake_redis):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await setup_chat(db)
        bot = RecordingBot()
        for _ in range(3):
            await on_message(
                make_message(text="спам http://spam.net"), bot, data_for(db, fake_redis)
            )
        assert bot.called("ban_chat_member") == 1
        async with db.session() as session:
            rows = (await session.execute(select(MessageLog))).scalars().all()
        assert [r.action for r in rows] == [
            "delete",
            "warn",
            "delete",
            "warn",
            "delete",
            "warn",
            "ban",
        ]
    finally:
        await db.dispose()


async def test_repeat_detected_within_window(fake_redis):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await setup_chat(db)
        bot = RecordingBot()
        text = "одинаковое сообщение дважды"
        assert await on_message(make_message(text=text), bot, data_for(db, fake_redis)) is False
        await on_message(make_message(text=text), bot, data_for(db, fake_redis))
        async with db.session() as session:
            rows = (await session.execute(select(MessageLog))).scalars().all()
        assert len(rows) == 2
        assert rows[0].filter_type == "repeat" and rows[0].action == "delete"
        assert rows[1].filter_type == "repeat" and rows[1].action == "warn"
    finally:
        await db.dispose()


async def test_admin_message_skipped(fake_redis):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await setup_chat(db)
        bot = RecordingBot()
        bot.member_status_by_user[42] = "administrator"
        msg = make_message(text="важно http://conf.ro")
        assert await on_message(msg, bot, data_for(db, fake_redis)) is False
        async with db.session() as session:
            rows = (await session.execute(select(MessageLog))).scalars().all()
        assert rows == []
        assert bot.called("delete_message") == 0
    finally:
        await db.dispose()


async def test_stopwords_gated_by_plan(fake_redis):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        chat = await get_or_create_chat(db.session_factory, -100)
        async with db.session() as session:
            db_chat = await session.get(type(chat), chat.id)
            db_chat.settings = default_settings()
            db_chat.settings["antispam"]["stopwords"] = ["развод"]
            db_chat.plan = "free"
            await session.commit()
        bot = RecordingBot()
        assert (
            await on_message(make_message(text="это развод друзья"), bot, data_for(db, fake_redis))
            is False
        )
        assert bot.called("delete_message") == 0
    finally:
        await db.dispose()
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.modules.antispam.handler'`.

- [ ] **Step 3: Реализовать `bot/modules/antispam/handler.py`**

```python
from __future__ import annotations

import hashlib

from aiogram import F, Bot, Router
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.models import Chat, FilterType, LogAction, Member, MessageLog
from bot.modules.antispam.filters import check_text
from bot.support.permissions import is_chat_admin

router = Router(name="antispam")


async def warn_member(
    session_factory: async_sessionmaker[AsyncSession], chat_pk: int, user_id: int
) -> int:
    async with session_factory() as session:
        member = (
            await session.execute(
                select(Member).where(Member.chat_id == chat_pk, Member.user_id == user_id)
            )
        ).scalar_one_or_none()
        if member is None:
            member = Member(chat_id=chat_pk, user_id=user_id)
            session.add(member)
            await session.flush()
        member.warn_count += 1
        await session.commit()
        return member.warn_count


async def log_action(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    chat_id: int,
    user_id: int,
    filter_type: str,
    action: str,
) -> None:
    async with session_factory() as session:
        session.add(
            MessageLog(
                chat_id=chat_id,
                user_id=user_id,
                filter_type=filter_type,
                action=action,
            )
        )
        await session.commit()


@router.message(F.chat.type.in_({"group", "supergroup"}), F.text)
async def on_message(message: Message, bot: Bot, data: dict) -> bool | None:
    if message.from_user is None or message.from_user.is_bot:
        return False
    session_factory: async_sessionmaker[AsyncSession] = data["session_factory"]
    redis = data["redis"]
    async with session_factory() as session:
        chat = (
            await session.execute(select(Chat).where(Chat.chat_id == message.chat.id))
        ).scalar_one_or_none()
        if chat is None:
            return False
        cfg = chat.settings.get("antispam", {})
    if not cfg.get("enabled", True):
        return False
    if await is_chat_admin(bot, message.chat.id, message.from_user.id):
        return False
    text = message.text or ""
    fired = check_text(text, cfg, chat.plan)
    if fired is None and cfg.get("repeat_cluster_enabled", True):
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        window = int(cfg.get("repeat_window_min", 5)) * 60
        key = f"repeat:{message.chat.id}:{message.from_user.id}:{text_hash}"
        if await redis.set(key, "1", nx=True, ex=window):
            return False
        fired = FilterType.REPEAT
    if fired is None:
        return False
    await bot.delete_message(message.chat.id, message.message_id)
    await log_action(
        session_factory,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        filter_type=fired.value,
        action=LogAction.DELETE.value,
    )
    behavior = cfg.get("behavior", {}).get(fired.value, "delete+warn")
    if behavior != "delete":
        warns = await warn_member(session_factory, chat.id, message.from_user.id)
        await log_action(
            session_factory,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            filter_type=fired.value,
            action=LogAction.WARN.value,
        )
        warn_limit = int(cfg.get("warn_limit", 3))
        if warns >= warn_limit:
            await bot.ban_chat_member(message.chat.id, message.from_user.id)
            await log_action(
                session_factory,
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                filter_type=fired.value,
                action=LogAction.BAN.value,
            )
    return None
```

- [ ] **Step 4: Убедиться, что тесты зелёные**

Run: `make test`
Expected: 62 passed (56 + 6 новых), 1 deselected.

- [ ] **Step 5: Линт, типы, код-стиль, коммит**

```bash
make format && make lint && make typecheck
git add bot/modules/antispam/handler.py tests/test_antispam.py
git commit -m "feat: antispam message handler with delete-warn-ban and repeat dedup"
```

---

### Task 6: Приветствие (`greeting`)

**Files:**
- Create: `bot/modules/greeting/__init__.py`, `bot/modules/greeting/handler.py`
- Create: `tests/test_greeting.py`

**Interfaces:**
- Consumes: `Chat` (План 1), `throttle`/`RateLimiter` (Task 1), `bot: Bot`, `User`.
- Produces: `async on_member_joined(session_factory, bot: Bot, limiter: RateLimiter, chat: Chat, user: User) -> None`. Читает `chat.settings["greeting"]` (`enabled`, `text`, `buttons` — список `{"text", "url"}`); если `show_branding` — дописывает строку `\n\nРаботает на базе @{username}` (username из `bot.me()`); отправку гейтит broadcast-троттлингом `rl:bcast:{chat.chat_id}`.

- [ ] **Step 1: Написать падающий тест `tests/test_greeting.py`**

```python
from aiogram.types import User

from bot.modules.greeting.handler import on_member_joined
from conftest import RecordingBot


def make_chat(*, settings: dict, show_branding: bool = True):
    from types import SimpleNamespace

    return SimpleNamespace(chat_id=-100, show_branding=show_branding, settings=settings)


SETTINGS = {
    "greeting": {
        "enabled": True,
        "text": "Добро пожаловать!",
        "buttons": [{"text": "Канал", "url": "https://t.me/ch"}],
    }
}


async def test_greeting_sends_text(fake_limiter):
    bot = RecordingBot()
    await on_member_joined(
        None,
        bot,
        fake_limiter,
        make_chat(settings=SETTINGS),
        User(id=5, is_bot=False, first_name="U"),
    )
    assert bot.called("send_message") == 1
    assert bot.calls[0][1][0] == -100
    assert "Добро пожаловать!" in bot.calls[0][1][1]


async def test_greeting_renders_buttons(fake_limiter):
    bot = RecordingBot()
    await on_member_joined(
        None,
        bot,
        fake_limiter,
        make_chat(settings=SETTINGS),
        User(id=5, is_bot=False, first_name="U"),
    )
    _, _, kwargs = bot.calls[0]
    markup = kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "Канал"
    assert markup.inline_keyboard[0][0].url == "https://t.me/ch"


async def test_greeting_appends_branding(fake_limiter):
    bot = RecordingBot()
    await on_member_joined(
        None,
        bot,
        fake_limiter,
        make_chat(settings=SETTINGS, show_branding=True),
        User(id=5, is_bot=False, first_name="U"),
    )
    assert "@mod_bot" in bot.calls[0][1][1]


async def test_greeting_disabled_does_nothing(fake_limiter):
    bot = RecordingBot()
    await on_member_joined(
        None,
        bot,
        fake_limiter,
        make_chat(settings={"greeting": {"enabled": False, "text": "x", "buttons": []}}),
        User(id=5, is_bot=False, first_name="U"),
    )
    assert bot.called("send_message") == 0
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.modules.greeting.handler'`.

- [ ] **Step 3: Реализовать `bot/modules/greeting/handler.py`**

`bot/modules/greeting/__init__.py` — пустой файл.

```python
from __future__ import annotations

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.models import Chat
from bot.support.ratelimit import RateLimiter, throttle


async def on_member_joined(
    session_factory: async_sessionmaker[AsyncSession] | None,
    bot: Bot,
    limiter: RateLimiter,
    chat: Chat,
    user: User,
) -> None:
    cfg = chat.settings.get("greeting", {})
    if not cfg.get("enabled", False):
        return
    text = cfg.get("text", "")
    if not text:
        return
    buttons = [
        [InlineKeyboardButton(text=item["text"], url=item["url"])]
        for item in cfg.get("buttons", [])
        if item.get("text") and item.get("url")
    ]
    if chat.show_branding:
        username = (await bot.me()).username or "bot"
        text = f"{text}\n\nРаботает на базе @{username}"
    markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    await throttle(limiter, key=f"rl:bcast:{chat.chat_id}", limit=1, window=60)
    await bot.send_message(chat.chat_id, text, reply_markup=markup)
```

- [ ] **Step 4: Убедиться, что тесты зелёные**

Run: `make test`
Expected: 66 passed (62 + 4 новых), 1 deselected.

- [ ] **Step 5: Линт, типы, коммит**

```bash
make format && make lint && make typecheck
git add bot/modules/greeting/__init__.py bot/modules/greeting/handler.py tests/test_greeting.py
git commit -m "feat: greeting message with buttons and branding"
```

---

### Task 7: Капча (`captcha`) — restrict, challenge, идемпотентный callback

**Files:**
- Create: `bot/modules/captcha/__init__.py`, `bot/modules/captcha/handler.py`
- Create: `tests/test_captcha.py`

**Interfaces:**
- Consumes: `CaptchaChallenge`, `CaptchaStatus`, `Chat` (План 1); `throttle`/`RateLimiter` (Task 1).
- Produces:
  - `captcha.router = Router(name="captcha")`; `@router.callback_query(F.data.contains("captcha:"))`
  - `async on_member_joined(session_factory, bot, limiter, chat: Chat, user: User) -> None` — при `captcha.enabled`: создаёт `CaptchaChallenge(status=pending, expires_at=now+window)`, `restrict_chat_member` с `ChatPermissions(can_send_messages=False)` и `until_date=expires_at`, шлёт сообщение с кнопкой `captcha:{challenge.id}` (broadcast-троттлинг перед отправкой).
  - `on_callback(callback_query, bot, data) -> None` — атомарный CAS `UPDATE ... WHERE status='pending' AND user_id=me` → при успехе возврат прав `FULL_PERMISSIONS` (`until_date=0`); повторное нажатие/чужой юзер — ответ «Вы уже прошли проверку», без повторного `restrict`.

- [ ] **Step 1: Написать падающий тест `tests/test_captcha.py`**

```python
from datetime import UTC, datetime

from aiogram.types import CallbackQuery, User
from sqlalchemy import select

from bot.db.base import Base
from bot.db.engine import create_database
from bot.models import CaptchaChallenge, CaptchaStatus, Chat as DbChat
from bot.modules.captcha.handler import on_callback, on_member_joined
from bot.support.chats import get_or_create_chat
from conftest import RecordingBot


def data_for(db):
    return {"session_factory": db.session_factory}


def make_callback(*, data: str, user_id: int = 5) -> CallbackQuery:
    return CallbackQuery(
        id="cb-1",
        from_user=User(id=user_id, is_bot=False, first_name="U"),
        chat_instance="c",
        data=data,
    )


async def test_captcha_start_restricts_and_creates_challenge(fake_limiter):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        chat = await get_or_create_chat(db.session_factory, -100)
        bot = RecordingBot()
        await on_member_joined(
            db.session_factory, bot, fake_limiter, chat, User(id=5, is_bot=False, first_name="U")
        )
        assert bot.called("restrict_chat_member") == 1
        assert bot.called("send_message") == 1
        args = bot.calls[0][1]
        assert args[0] == -100 and args[1] == 5
        perms = bot.calls[0][2]["permissions"]
        assert perms.can_send_messages is False
        async with db.session() as session:
            ch = (await session.execute(select(CaptchaChallenge))).scalar_one()
            msg = bot.calls[1][2]["reply_markup"].inline_keyboard[0][0]
        assert ch.status == CaptchaStatus.PENDING.value
        assert msg.callback_data == f"captcha:{ch.id}"
    finally:
        await db.dispose()


async def test_captcha_disabled_does_nothing(fake_limiter):
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        chat = await get_or_create_chat(db.session_factory, -100)
        async with db.session() as session:
            db_chat = await session.get(DbChat, chat.id)
            db_chat.settings["captcha"]["enabled"] = False
            await session.commit()
        bot = RecordingBot()
        await on_member_joined(
            db.session_factory, bot, fake_limiter, chat, User(id=5, is_bot=False, first_name="U")
        )
        assert bot.called("restrict_chat_member") == 0
        assert bot.called("send_message") == 0
    finally:
        await db.dispose()


async def test_callback_done_restores_permissions():
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        chat = await get_or_create_chat(db.session_factory, -100)
        async with db.session() as session:
            challenge = CaptchaChallenge(
                chat_id=chat.id,
                user_id=5,
                status=CaptchaStatus.PENDING.value,
                expires_at=datetime.now(UTC),
            )
            session.add(challenge)
            await session.commit()
            ch_id = challenge.id
        bot = RecordingBot()
        await on_callback(make_callback(data=f"captcha:{ch_id}"), bot, data_for(db))
        assert bot.called("restrict_chat_member") == 1
        args = bot.calls[0][1]
        assert args[0] == -100 and args[1] == 5
        perms = bot.calls[0][2]["permissions"]
        assert perms.can_send_messages is True
        async with db.session() as session:
            fresh = await session.get(CaptchaChallenge, ch_id)
        assert fresh.status == CaptchaStatus.DONE.value
    finally:
        await db.dispose()


async def test_callback_twice_is_idempotent():
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        chat = await get_or_create_chat(db.session_factory, -100)
        async with db.session() as session:
            challenge = CaptchaChallenge(
                chat_id=chat.id,
                user_id=5,
                status=CaptchaStatus.PENDING.value,
                expires_at=datetime.now(UTC),
            )
            session.add(challenge)
            await session.commit()
            ch_id = challenge.id
        bot = RecordingBot()
        await on_callback(make_callback(data=f"captcha:{ch_id}"), bot, data_for(db))
        await on_callback(make_callback(data=f"captcha:{ch_id}"), bot, data_for(db))
        assert bot.called("restrict_chat_member") == 1
        assert bot.called("answer_callback_query") == 2
    finally:
        await db.dispose()


async def test_callback_wrong_user_ignored():
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        chat = await get_or_create_chat(db.session_factory, -100)
        async with db.session() as session:
            challenge = CaptchaChallenge(
                chat_id=chat.id,
                user_id=5,
                status=CaptchaStatus.PENDING.value,
                expires_at=datetime.now(UTC),
            )
            session.add(challenge)
            await session.commit()
            ch_id = challenge.id
        bot = RecordingBot()
        await on_callback(make_callback(data=f"captcha:{ch_id}", user_id=999), bot, data_for(db))
        assert bot.called("restrict_chat_member") == 0
    finally:
        await db.dispose()
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.modules.captcha.handler'`.

- [ ] **Step 3: Реализовать `bot/modules/captcha/handler.py`**

`bot/modules/captcha/__init__.py` — пустой файл.

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import Bot, Router, F
from aiogram.types import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    User,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.models import CaptchaChallenge, CaptchaStatus, Chat
from bot.support.ratelimit import RateLimiter, throttle

router = Router(name="captcha")

FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_react_to_messages=True,
)


async def on_member_joined(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    limiter: RateLimiter,
    chat: Chat,
    user: User,
) -> None:
    cfg = chat.settings.get("captcha", {})
    if not cfg.get("enabled", True):
        return
    window_min = int(cfg.get("window_min", 10))
    expires_at = datetime.now(UTC) + timedelta(minutes=window_min)
    async with session_factory() as session:
        challenge = CaptchaChallenge(
            chat_id=chat.id,
            user_id=user.id,
            status=CaptchaStatus.PENDING.value,
            expires_at=expires_at,
        )
        session.add(challenge)
        await session.commit()
        challenge_id = challenge.id
    await bot.restrict_chat_member(
        chat.chat_id,
        user.id,
        permissions=ChatPermissions(can_send_messages=False),
        until_date=int(expires_at.timestamp()),
    )
    await throttle(limiter, key=f"rl:bcast:{chat.chat_id}", limit=1, window=60)
    await bot.send_message(
        chat.chat_id,
        "Для продолжения нажмите кнопку ниже",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👤 Я человек", callback_data=f"captcha:{challenge_id}")]
            ]
        ),
    )


@router.callback_query(F.data.contains("captcha:"))
async def on_callback(callback_query: CallbackQuery, bot: Bot, data: dict) -> None:
    session_factory: async_sessionmaker[AsyncSession] = data["session_factory"]
    try:
        challenge_id = int(callback_query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await bot.answer_callback_query(callback_query.id)
        return
    user_id = callback_query.from_user.id
    async with session_factory() as session:
        result = await session.execute(
            update(CaptchaChallenge)
            .where(
                CaptchaChallenge.id == challenge_id,
                CaptchaChallenge.status == CaptchaStatus.PENDING.value,
                CaptchaChallenge.user_id == user_id,
            )
            .values(status=CaptchaStatus.DONE.value)
        )
        if result.rowcount == 0:
            await bot.answer_callback_query(callback_query.id, text="Вы уже прошли проверку")
            return
        await session.commit()
        chat = (
            await session.execute(
                select(Chat)
                .join(CaptchaChallenge, CaptchaChallenge.chat_id == Chat.id)
                .where(CaptchaChallenge.id == challenge_id)
            )
        ).scalar_one_or_none()
    if chat is not None:
        await bot.restrict_chat_member(
            chat.chat_id, user_id, permissions=FULL_PERMISSIONS, until_date=0
        )
    await bot.answer_callback_query(callback_query.id, text="✅", cache_time=30)
```

- [ ] **Step 4: Убедиться, что тесты зелёные**

Run: `make test`
Expected: 71 passed (66 + 5 новых), 1 deselected.

- [ ] **Step 5: Линт, типы, коммит**

```bash
make format && make lint && make typecheck
git add bot/modules/captcha/__init__.py bot/modules/captcha/handler.py tests/test_captcha.py
git commit -m "feat: captcha flow with restrict, challenge button and idempotent callback"
```

---

### Task 8: Подключение модулей в `app.py` и проверка smoke

**Files:**
- Modify: `bot/app.py` (`create_dispatcher` — принимает `redis`, вешает роутеры и workflow-данные; `run` — строит `RateLimiter`, кастомную сессию для `Bot`)
- Modify: `tests/test_health.py` (обновить тест `test_create_dispatcher_registers_middleware`)

**Interfaces:**
- Consumes: `RateLimiter` (Task 1), `create_session` (Task 1), `chatflow.router`, `antispam.router`, `captcha.router` (Tasks 3/5/7), `redis.asyncio.Redis`.
- Produces: `create_dispatcher(db: Database, redis: Redis) -> Dispatcher` — workflow-данные `dp["session_factory"]`, `dp["redis"]`, `dp["rate_limiter"]`; инклудит роутеры. Хендлеры читают зависимости из `data` по этим ключам.

- [ ] **Step 1: Обновить тест `tests/test_health.py`**

Заменить последний тест:

```python
class FakeRedis:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    async def ping(self):
        if not self.ok:
            raise RuntimeError("redis down")
        return True


def test_create_dispatcher_registers_middleware():
    from bot.app import create_dispatcher

    dp = create_dispatcher(FakeDB(True), FakeRedis(True))
    assert dp is not None
```

(класс `FakeRedis` уже определён в файле — только заменить тело `test_create_dispatcher_registers_middleware` на вызов с двумя аргументами и импорт `create_dispatcher` внутрь функции.)

- [ ] **Step 2: Убедиться, что тест падает**

Run: `make test`
Expected: FAIL — `TypeError: create_dispatcher() takes 1 positional argument but 2 were given`.

- [ ] **Step 3: Изменить `bot/app.py`**

В импорты добавить:

```python
from bot.modules.captcha.handler import on_member_joined as captcha_on_member_joined
from bot.modules.captcha.handler import router as captcha_router
from bot.modules.chatflow import router as chatflow_router
from bot.modules.antispam.handler import router as antispam_router
from bot.modules.greeting.handler import on_member_joined as greeting_on_member_joined
from bot.support.ratelimit import RateLimiter
from bot.support.sessions import create_session
```

Заменить `create_dispatcher` и `run` на:

```python
def create_dispatcher(db: Database, redis: Redis) -> Dispatcher:
    dp = Dispatcher()
    dp["session_factory"] = db.session_factory
    dp["redis"] = redis
    dp["rate_limiter"] = RateLimiter(redis)
    dp["captcha_on_member_joined"] = captcha_on_member_joined
    dp["greeting_on_member_joined"] = greeting_on_member_joined
    register_error_handlers(dp, db.session_factory)
    dp.include_router(chatflow_router)
    dp.include_router(antispam_router)
    dp.include_router(captcha_router)
    return dp


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    db = create_database(settings.database_url)
    redis = create_redis(settings.redis_url)
    rate_limiter = RateLimiter(redis)
    session = create_session(rate_limiter)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )
    dp = create_dispatcher(db, redis)
    runner: web.AppRunner | None = None
    try:
        if settings.mode == "polling":
            runner = web.AppRunner(build_health_app(db, redis))
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", settings.health_port).start()
            log.info("starting polling mode, health on :%s/health", settings.health_port)
            await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
        else:
            app = build_health_app(db, redis)

            async def webhook_handler(request: web.Request) -> web.Response:
                update = Update.model_validate(await request.json())
                await dp.feed_update(bot=bot, update=update)
                return web.Response(text="ok")

            app.router.add_post(settings.webhook_path, webhook_handler)
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", settings.health_port).start()
            await bot.set_webhook(
                url=f"{settings.webhook_url}{settings.webhook_path}",
                allowed_updates=ALLOWED_UPDATES,
                drop_pending_updates=True,
            )
            log.info("webhook mode, listening on :%s", settings.health_port)
            await asyncio.Event().wait()
    finally:
        if runner is not None:
            await runner.cleanup()
        await bot.session.close()
        await redis.aclose()
        await db.dispose()
```
- [ ] **Step 4: Убедиться, что все тесты зелёные**

Run: `make test`
Expected: **71 passed**, 1 deselected.

Run: `make test-integration` (миграции и БД не менялись — должно остаться зелёным)
Expected: 1 passed, 71 deselected.

- [ ] **Step 5: Линт, типы, коммит**

```bash
make format && make lint && make typecheck
```

**Внимание:** `make typecheck` (mypy) теперь проверяет и новые модули; если mypy ругается на неаннотированные параметры в `sessions.py`/`handler.py` — аннотации в этих файлах уже заданы; исправить всё, что всплывёт, и добиться зелёного, прежде чем коммитить.

```bash
git add bot/app.py tests/test_health.py
git commit -m "feat: wire moderation routers and rate-limited session into app entrypoint"
```

- [ ] **Step 6: Smoke-проверка**

Поднять стек и запустить бота с фейковым токеном (health-сервер живёт без валидного токена):

```bash
make up
BOT_TOKEN=1234:dummy MODE=polling DATABASE_URL='postgresql+psycopg://bot:bot@localhost:5432/botdb' \
REDIS_URL='redis://localhost:6379/0' HEALTH_PORT=8080 .venv/bin/python -m bot.app &
```

Проверить, что `curl -s localhost:8080/health` возвращает `{"status":"ok","db":true,"redis":true}`, затем остановить процесс. Ожидаемо, что polling с невалидным токеном завершится ошибкой авторизации — это не провал (в проде токен валидный).

---

## Self-review (после написания плана)

- **Спека:** раздел «Онбординг чата» → Task 3 (abuse-limit 10, `member_count`, archived/restore); «Анти-спам» (4 фильтра, `delete`/`delete+warn`, warn_limit/бан, pro-гейт) → Tasks 4–5; «Приветствие» (текст, кнопки-ссылки, брендинг) → Task 6; «Капча» (restrict с явными permissions, кнопка, атомарная идемпотентность `status=pending`→`done`) → Task 7; «Rate limits / Flood control» (3 уровня, очередь/ожидание вместо пачки, `retry_after`) → Task 1; «Redis» (эфемерность, дедуп) → Tasks 1, 5; «Модели БД» (FK на `chats.id` vs внешний `chat_id`) → везде.
- **Таймауты капчи** и входящий троттлинг хендлеров — сознательно в Плане 3 (worker), как и в декомпозиции Плана 1.
- **Типы/имена** перепроверены по фактическим сигнатурам aiogram 3.31 (`BaseRequestMiddleware.__call__(make_request, bot, method)`, `TelegramRetryAfter(method, message, retry_after)`, `Bot.me()`, `ChatMemberUpdated(chat, from_user, date, old_chat_member, new_chat_member)`, `ChatMemberMember(user=...)`).
- **Счётчики:** 24 → 33 → 40 → 44 → 56 → 62 → 66 → 71 (unit), integration остаётся 1.