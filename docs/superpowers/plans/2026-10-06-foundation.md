# Foundation Implementation Plan (каркас, БД, миграции, точка входа)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Рабочий каркас SaaS-бота-модератора: проект ставится, конфигурируется из env, схема БД создаётся алембиком (с месячными партициями), точка входа поднимается в режиме polling/webhook с health-эндпоинтом, ошибки handlers пишутся в ErrorLog.

**Architecture:** Модульный монолит на aiogram 3: один процесс `bot` обслуживает все чаты. Это **План 1 из 3** — он создаёт фундамент, на котором План 2 (модули antispam/greeting/captcha + rate limiting) и План 3 (worker, шедулер, статистика, billing, мониторинг) наращивают функциональность без изменения ядра.

**Tech Stack:** Python 3.12+ (окружение — системный Python 3.14), aiogram 3.31, SQLAlchemy 2 async + psycopg3, alembic, pydantic-settings, redis-py, aiohttp, pytest + pytest-asyncio (aiosqlite для юнит-тестов), ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-10-06-community-mod-bot-design.md` — аргументы ниже выводятся из спеки; читать её вместе с планом.

**Декомпозиция спеки на планы:**

- **План 1 (этот):** каркас, Settings, БД-движок, все ORM-модели, alembic 0001 с партициями, docker-compose (postgres+redis), ErrorLog-middleware, проверка прав, точка входа, `/health`.
- **План 2:** multitenancy-флоу (chat_member: добавление/архив/bulk-limit), antispam, greeting, captcha, outbound rate limiting (3 уровня, кастомная aiogram-сессия), Redis-состояние.
- **План 3:** worker (APScheduler), автопостинг, статистика + `make seed`, billing (Stars, renewal-реминдеры, grace period), Dockerfile + сервисы bot/worker в compose, Prometheus-метрики, alert-и суперадмину.

## Global Constraints

- Python `>=3.12`; aiogram `>=3.24,<4` (в окружении 3.31.0); SQLAlchemy `>=2.0.30`; psycopg3 (`psycopg[binary]`).
- PostgreSQL — единственная СУБД в проде; юнит-тесты — только `aiosqlite` (без партиционирования); интеграционные тесты — реальный Postgres через `TEST_POSTGRES_URL`.
- Redis хранит только эфемерное состояние (троттлинг, капча, дедуп) — долгоживущее только в PostgreSQL (спека, «Redis»).
- Текст сообщений в `MessageLog` не храним — только метаданные (спека, «Модели БД»).
- Секреты (BOT_TOKEN) — только в env; не пишем в БД, не логируем; объект `Settings` целиком не логировать.
- Все chat_id/user_id — `BigInteger`; парольных полей в БД нет.
- Схема: `MessageLog` и `ChatMemberEvents` — партиционированы по месяцам `RANGE (created_at)`; партиции текущего и следующего месяца создаёт миграция 0001 (worker будет создавать будущие — План 3).
- Все констрейнты (PK/FK/UQ/IX) получают **явные имена** и в моделях, и в DDL — они обязаны совпадать буква в букву (проверяется интеграционным тестом через `alembic check`).
- Идентификаторы в коде — английские; комментарии в код не добавляем.
- Перед каждым коммитом: `make format && make lint && make typecheck && make test` — всё зелёное.
- Коммиты только файлов внутри `tg_botSaas/` (в репозитории корень — папка `фриланс`, есть незакоммиченные удаления `../tgbot`, `../AGENTS.md` — их НЕ трогать, `git add` только по явным путям текущего проекта).
- Commit message — conventional commits (`feat:`, `test:`, `chore:`).

## Структура файлов (итог Плана 1)

```
tg_botSaas/
  pyproject.toml              # deps, ruff, mypy, pytest
  Makefile                    # install/test/lint/typecheck/up/down/migrate/run
  docker-compose.yml          # postgres + redis (+ bot/worker добавит План 3)
  .env.example                # все переменные окружения
  .gitignore
  ops/postgres/init.sql       # CREATE DATABASE bot_test (для интеграционных тестов)
  bot/
    __init__.py
    app.py                    # точка входа: polling/webhook + /health
    config.py                 # Settings (pydantic-settings)
    models.py                 # все ORM-модели
    db/
      __init__.py
      base.py                 # Base + naming convention
      engine.py               # Database: engine, сессии, ping, dispose
    support/
      __init__.py
      errors.py               # ErrorLogMiddleware, record_error
      permissions.py          # is_chat_admin
      redis.py                # create_redis
  migrations/
    env.py                    # async-рецепт alembic
    script.py.mako
    versions/0001_initial.py  # все таблицы + партиции
  alembic.ini
  tests/
    conftest.py
    test_config.py
    test_db.py
    test_models.py
    test_migrations.py        # интеграционный (mark=integration)
    test_errors.py
    test_permissions.py
    test_health.py
```

---

### Task 1: Каркас проекта и настройки (Settings)

**Files:**
- Create: `pyproject.toml`, `Makefile`, `.gitignore`, `.env.example`
- Create: `bot/__init__.py`, `bot/config.py`
- Create: `tests/conftest.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: env-переменные (`BOT_TOKEN`, `MODE`, `DATABASE_URL`, `REDIS_URL`, `SUPERADMIN_IDS`, `HEALTH_PORT`, `LOG_LEVEL`, `STARS_PRO_PRICE`, `WEBHOOK_URL`, `WEBHOOK_PATH`).
- Produces:
  - `bot.config.Settings` — поля: `bot_token: str`, `mode: Literal["polling","webhook"]`, `webhook_url: str | None`, `webhook_path: str`, `database_url: str`, `redis_url: str`, `superadmin_ids: str`, `health_port: int`, `log_level: str`, `stars_pro_price: int`; property `superadmins -> frozenset[int]`.
  - `bot.config.get_settings() -> Settings` (lru_cache; в тестах сбрасывать `get_settings.cache_clear()` — conftest это делает).
  - `make test / lint / typecheck / format` — команды проверки для всех последующих задач.

- [ ] **Step 1: Создать каркас проекта**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "tg-bot-saas"
version = "0.1.0"
description = "Telegram moderation bot SaaS for large communities"
requires-python = ">=3.12"
dependencies = [
    "aiogram>=3.24,<4",
    "sqlalchemy[asyncio]>=2.0.30",
    "psycopg[binary]>=3.2",
    "alembic>=1.13",
    "pydantic-settings>=2.5",
    "redis>=5.2",
    "aiohttp>=3.10",
    "apscheduler>=3.10,<4",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "aiosqlite>=0.20",
    "ruff>=0.8",
    "mypy>=1.13",
]

[tool.setuptools.packages.find]
include = ["bot*"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
python_version = "3.12"
check_untyped_defs = true
warn_unused_ignores = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]
markers = [
    "integration: requires TEST_POSTGRES_URL and a running PostgreSQL",
]
```

`Makefile`:

```make
PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
COMPOSE ?= podman compose
TEST_DB_URL ?= postgresql+psycopg://bot:bot@localhost:5432/bot_test

install:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -e ".[dev]"

test:
	$(BIN)/pytest -q -m "not integration"

test-integration:
	TEST_POSTGRES_URL=$(TEST_DB_URL) $(BIN)/pytest -q -m integration

lint:
	$(BIN)/ruff check .

format:
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

typecheck:
	$(BIN)/mypy bot

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down -v

migrate:
	$(BIN)/alembic upgrade head

run:
	$(BIN)/python -m bot.app
```

`.gitignore`:

```
.venv/
__pycache__/
*.py[cod]
*.egg-info/
.env
.mypy_cache/
.pytest_cache/
.ruff_cache/
dist/
build/
```

`.env.example`:

```
BOT_TOKEN=123456:REPLACE_ME
MODE=polling
DATABASE_URL=postgresql+psycopg://bot:bot@localhost:5432/botdb
REDIS_URL=redis://localhost:6379/0
SUPERADMIN_IDS=111111111
HEALTH_PORT=8080
LOG_LEVEL=INFO
STARS_PRO_PRICE=150
WEBHOOK_URL=
WEBHOOK_PATH=/tg/webhook
```

`bot/__init__.py` — пустой файл.

- [ ] **Step 2: Написать падающий тест настройки**

`tests/conftest.py`:

```python
import pytest

from bot.config import get_settings


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:TEST")
    monkeypatch.setenv("MODE", "polling")
    monkeypatch.setenv("SUPERADMIN_IDS", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
```

`tests/test_config.py`:

```python
from pydantic import ValidationError

from bot.config import Settings


def test_defaults(monkeypatch):
    s = Settings()
    assert s.mode == "polling"
    assert s.database_url.startswith("postgresql+psycopg://")
    assert s.redis_url.startswith("redis://")
    assert s.health_port == 8080
    assert s.stars_pro_price == 150
    assert s.superadmins == frozenset()


def test_superadmins_parsing(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_IDS", "10, 20,30")
    s = Settings()
    assert s.superadmins == frozenset({10, 20, 30})


def test_webhook_mode_requires_url(monkeypatch):
    monkeypatch.setenv("MODE", "webhook")
    monkeypatch.setenv("WEBHOOK_URL", "")
    try:
        Settings()
    except ValidationError:
        pass
    else:
        raise AssertionError("webhook mode without WEBHOOK_URL must fail")


def test_webhook_mode_with_url_ok(monkeypatch):
    monkeypatch.setenv("MODE", "webhook")
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com")
    s = Settings()
    assert s.webhook_path == "/tg/webhook"
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `make install && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.config'` (collection error).

- [ ] **Step 4: Реализовать `bot/config.py`**

```python
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    mode: Literal["polling", "webhook"] = "polling"
    webhook_url: str | None = None
    webhook_path: str = "/tg/webhook"
    database_url: str = "postgresql+psycopg://bot:bot@localhost:5432/botdb"
    redis_url: str = "redis://localhost:6379/0"
    superadmin_ids: str = ""
    health_port: int = 8080
    log_level: str = "INFO"
    stars_pro_price: int = 150

    @field_validator("bot_token")
    @classmethod
    def bot_token_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("bot_token must not be empty")
        return value

    @model_validator(mode="after")
    def check_webhook(self) -> Settings:
        if self.mode == "webhook" and not self.webhook_url:
            raise ValueError("webhook_url is required when mode=webhook")
        return self

    @property
    def superadmins(self) -> frozenset[int]:
        parts = [p.strip() for p in self.superadmin_ids.split(",") if p.strip()]
        return frozenset(int(p) for p in parts)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 5: Запустить тесты — проверить PASS**

Run: `make test`
Expected: `4 passed`.

- [ ] **Step 6: Форматирование, линт, типы**

Run: `make format && make lint && make typecheck`
Expected: без ошибок.

- [ ] **Step 7: Коммит**

```bash
git add pyproject.toml Makefile .gitignore .env.example bot/__init__.py bot/config.py tests/conftest.py tests/test_config.py
git commit -m "feat: project scaffold and settings"
```

---

### Task 2: Async-движок БД и сессии

**Files:**
- Create: `bot/db/__init__.py`, `bot/db/base.py`, `bot/db/engine.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `Settings.database_url` из Task 1.
- Produces:
  - `bot.db.base.Base` — `DeclarativeBase` c naming convention (`pk_%(table_name)s`, `fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s`, `uq_%(table_name)s_%(column_0_name)s`, `ix_%(column_0_label)s`, `ck_%(table_name)s_%(constraint_name)s`).
  - `bot.db.engine.create_database(url: str) -> Database`
  - `Database.session()` — async context manager → `AsyncSession`
  - `Database.session_factory: async_sessionmaker[AsyncSession]` (передаётся в `register_error_handlers`, Task 5)
  - `Database.ping() -> bool`, `Database.dispose() -> None`

- [ ] **Step 1: Написать падающий тест**

`tests/test_db.py`:

```python
from sqlalchemy import text

from bot.db.base import Base
from bot.db.engine import create_database


def test_base_is_declarative_with_naming_convention():
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"
    assert Base.metadata.naming_convention["fk"] == (
        "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    )


async def test_ping_and_session():
    db = create_database("sqlite+aiosqlite://")
    try:
        assert await db.ping() is True
        async with db.session() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await db.dispose()


async def test_ping_false_on_unreachable_db():
    db = create_database("postgresql+psycopg://bot:bot@127.0.0.1:1/none")
    try:
        assert await db.ping() is False
    finally:
        await db.dispose()
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.db'`.

- [ ] **Step 3: Реализовать движок**

`bot/db/__init__.py` — пустой.

`bot/db/base.py`:

```python
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ix": "ix_%(column_0_label)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

`bot/db/engine.py`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(url, pool_pre_ping=True)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def dispose(self) -> None:
        await self.engine.dispose()


def create_database(url: str) -> Database:
    return Database(url)
```

- [ ] **Step 4: Запустить тесты — проверить PASS**

Run: `make test`
Expected: все тесты зелёные (7 passed: 4 из Task 1 + 3 новых).

- [ ] **Step 5: Форматирование, линт, типы**

Run: `make format && make lint && make typecheck`
Expected: без ошибок.

- [ ] **Step 6: Коммит**

```bash
git add bot/db tests/test_db.py
git commit -m "feat: async database engine and sessions"
```

---

### Task 3: ORM-модели (все таблицы схемы)

**Files:**
- Create: `bot/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `Base` из `bot.db.base` (Task 2).
- Produces (используются всеми последующими планами):
  - Модели: `Chat`, `Moderator`, `Member`, `MessageLog`, `ChatMemberEvents`, `DailyStats`, `CaptchaChallenge`, `ScheduledTask`, `PaidPlan`, `ErrorLog`.
  - enum-значения (хранятся как строки): `Plan` (`free/pro/enterprise`), `FilterType` (`link/caps/repeat/stopword`), `LogAction` (`delete/warn/ban`), `CaptchaStatus` (`pending/done/expired`), `MemberEvent` (`join/leave`).
  - `utcnow() -> datetime` — единственная функция текущего времени (UTC, tz-aware).
  - `JSON_TYPE` — `JSON().with_variant(JSONB(), "postgresql")` (колонка `chats.settings`).
  - Важно: `MessageLog` и `ChatMemberEvents` имеют **составной PK `(id, created_at)`**, где `id` — `Uuid` с Python-дефолтом `uuid.uuid4` (так и ORM-вставки, и партиционирование в PG работают без sequence; surrogate id генерируется на стороне приложения).

- [ ] **Step 1: Написать падающий тест**

`tests/test_models.py`:

```python
import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.base import Base
from bot.models import (
    CaptchaChallenge,
    Chat,
    DailyStats,
    FilterType,
    LogAction,
    Member,
    MessageLog,
    Plan,
    utcnow,
)


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def test_chat_json_settings_roundtrip(session_factory):
    async with session_factory() as session:
        session.add(
            Chat(chat_id=-1001, settings={"antispam": {"links": True}}, plan=Plan.PRO.value)
        )
        await session.commit()
    async with session_factory() as session:
        loaded = (await session.execute(select(Chat).where(Chat.chat_id == -1001))).scalar_one()
        assert loaded.settings["antispam"]["links"] is True
        assert loaded.plan == "pro"
        assert loaded.member_count == 0
        assert loaded.show_branding is True
        assert loaded.archived is False


async def test_chat_chat_id_unique(session_factory):
    async with session_factory() as session:
        session.add(Chat(chat_id=-1002))
        await session.commit()
    async with session_factory() as session:
        session.add(Chat(chat_id=-1002))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_member_unique_per_chat(session_factory):
    async with session_factory() as session:
        chat = Chat(chat_id=-1003)
        session.add(chat)
        await session.flush()
        session.add(Member(chat_id=chat.id, user_id=7))
        await session.commit()
    async with session_factory() as session:
        chat = (await session.execute(select(Chat).where(Chat.chat_id == -1003))).scalar_one()
        session.add(Member(chat_id=chat.id, user_id=7))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_message_log_defaults(session_factory):
    async with session_factory() as session:
        session.add(
            MessageLog(
                chat_id=-1004,
                user_id=7,
                filter_type=FilterType.CAPS.value,
                action=LogAction.WARN.value,
            )
        )
        await session.commit()
    async with session_factory() as session:
        row = (await session.execute(select(MessageLog))).scalar_one()
        assert isinstance(row.id, uuid.UUID)
        assert row.created_at is not None


async def test_daily_stats_unique_day(session_factory):
    async with session_factory() as session:
        chat = Chat(chat_id=-1005)
        session.add(chat)
        await session.flush()
        session.add(DailyStats(chat_id=chat.id, day=date(2026, 10, 6)))
        await session.commit()
    async with session_factory() as session:
        chat = (await session.execute(select(Chat).where(Chat.chat_id == -1005))).scalar_one()
        session.add(DailyStats(chat_id=chat.id, day=date(2026, 10, 6)))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_captcha_status_default(session_factory):
    async with session_factory() as session:
        chat = Chat(chat_id=-1006)
        session.add(chat)
        await session.flush()
        session.add(CaptchaChallenge(chat_id=chat.id, user_id=9, expires_at=utcnow()))
        await session.commit()
    async with session_factory() as session:
        challenge = (await session.execute(select(CaptchaChallenge))).scalar_one()
        assert challenge.status == "pending"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.models'`.

- [ ] **Step 3: Реализовать `bot/models.py`**

```python
from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.base import Base

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Plan(enum.StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class FilterType(enum.StrEnum):
    LINK = "link"
    CAPS = "caps"
    REPEAT = "repeat"
    STOPWORD = "stopword"


class LogAction(enum.StrEnum):
    DELETE = "delete"
    WARN = "warn"
    BAN = "ban"


class CaptchaStatus(enum.StrEnum):
    PENDING = "pending"
    DONE = "done"
    EXPIRED = "expired"


class MemberEvent(enum.StrEnum):
    JOIN = "join"
    LEAVE = "leave"


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(String(255))
    settings: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    plan: Mapped[str] = mapped_column(String(16), default=Plan.FREE.value)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    show_branding: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_chats"),
        UniqueConstraint("chat_id", name="uq_chats_chat_id"),
    )


class Moderator(Base):
    __tablename__ = "moderators"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_moderators_chat_id_chats")
    )
    user_id: Mapped[int] = mapped_column(BigInteger)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_moderators"),
        UniqueConstraint("chat_id", "user_id", name="uq_moderators_chat_id_user_id"),
    )


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_members_chat_id_chats")
    )
    user_id: Mapped[int] = mapped_column(BigInteger)
    warn_count: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_members"),
        UniqueConstraint("chat_id", "user_id", name="uq_members_chat_id_user_id"),
    )


class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    filter_type: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_message_logs"),
        Index("ix_message_logs_chat_created_at", "chat_id", "created_at"),
    )


class ChatMemberEvents(Base):
    __tablename__ = "chat_member_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_chat_member_events"),
        Index("ix_chat_member_events_chat_created_at", "chat_id", "created_at"),
    )


class DailyStats(Base):
    __tablename__ = "daily_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_daily_stats_chat_id_chats")
    )
    day: Mapped[date] = mapped_column(Date)
    active_users: Mapped[int] = mapped_column(Integer, default=0)
    new_members: Mapped[int] = mapped_column(Integer, default=0)
    left_members: Mapped[int] = mapped_column(Integer, default=0)
    total_messages: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_daily_stats"),
        UniqueConstraint("chat_id", "day", name="uq_daily_stats_chat_id_day"),
    )


class CaptchaChallenge(Base):
    __tablename__ = "captcha_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_captcha_challenges_chat_id_chats")
    )
    user_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default=CaptchaStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_captcha_challenges"),
        Index("ix_captcha_challenges_chat_id_status", "chat_id", "status"),
    )


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_scheduled_tasks_chat_id_chats")
    )
    cron: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (PrimaryKeyConstraint("id", name="pk_scheduled_tasks"),)


class PaidPlan(Base):
    __tablename__ = "paid_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_paid_plans_chat_id_chats")
    )
    plan: Mapped[str] = mapped_column(String(16))
    stars_amount: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    charge_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_paid_plans"),
        UniqueConstraint("charge_id", name="uq_paid_plans_charge_id"),
    )


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    error_type: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_error_logs"),
        Index("ix_error_logs_created_at", "created_at"),
    )
```

- [ ] **Step 4: Запустить тесты — проверить PASS**

Run: `make test`
Expected: `13 passed` (4 из Task 1 + 3 из Task 2 + 6 новых).

- [ ] **Step 5: Форматирование, линт, типы**

Run: `make format && make lint && make typecheck`
Expected: без ошибок.

- [ ] **Step 6: Коммит**

```bash
git add bot/models.py tests/test_models.py
git commit -m "feat: core ORM models for all tables"
```

---

### Task 4: Alembic 0001 (схема + партиции) и docker-compose

**Files:**
- Create: `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_initial.py`
- Create: `docker-compose.yml`, `ops/postgres/init.sql`
- Test: `tests/test_migrations.py`
- Modify: `Makefile` (добавить цели `up`, `down`, `test-integration` — они уже описаны в Task 1, при необходимости синхронизировать)

**Interfaces:**
- Consumes: `Base.metadata` и модели из Task 3; `Settings.database_url` как fallback URL для миграций.
- Produces:
  - `alembic upgrade head` (из корня проекта) — создаёт всю схему, включая parent-таблицы `message_logs`, `chat_member_events` и партиции текущего+следующего месяца.
  - Интеграционный контракт: `TEST_POSTGRES_URL` (база `bot_test`, создаётся init-скриптом compose) — тест сбрасывает `public` schema, гоняет `upgrade head` + `alembic check` (zero schema drift между моделями и DDL) + проверяет, что вставка в `message_logs` попадает в месячную партицию.
  - `DATABASE_URL` — override URL для алембика (иначе — `Settings.database_url`).

- [ ] **Step 1: Написать падающий интеграционный тест**

`tests/test_migrations.py`:

```python
import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.integration


@pytest.mark.skipif("TEST_POSTGRES_URL" not in os.environ, reason="TEST_POSTGRES_URL is not set")
def test_upgrade_schema_check_and_partition_insert(monkeypatch):
    url = os.environ["TEST_POSTGRES_URL"]
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    command.check(cfg)

    async def roundtrip() -> tuple[str, int]:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from bot.models import Chat, MessageLog

        eng = create_async_engine(url)
        async with async_sessionmaker(eng, expire_on_commit=False)() as session:
            chat = Chat(chat_id=-100999, title="integration")
            session.add(chat)
            await session.flush()
            session.add(
                MessageLog(
                    chat_id=chat.chat_id,
                    user_id=1,
                    filter_type="link",
                    action="delete",
                )
            )
            await session.commit()
        async with eng.connect() as conn:
            tableoid = (
                await conn.execute(text("SELECT tableoid::regclass::text FROM message_logs"))
            ).scalar_one()
            count = (await conn.execute(text("SELECT count(*) FROM message_logs"))).scalar_one()
        await eng.dispose()
        return tableoid, count

    tableoid, count = asyncio.run(roundtrip())
    assert count == 1
    assert tableoid.startswith("message_logs_")
```

- [ ] **Step 2: Написать compose и init-скрипт (инфраструктура для теста)**

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: docker.io/library/postgres:16-alpine
    environment:
      POSTGRES_USER: bot
      POSTGRES_PASSWORD: bot
      POSTGRES_DB: botdb
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./ops/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U bot -d botdb"]
      interval: 5s
      timeout: 3s
      retries: 12
  redis:
    image: docker.io/library/redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 12

volumes:
  pgdata:
```

`ops/postgres/init.sql`:

```sql
CREATE DATABASE bot_test;
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `make up` (первый запуск скачает образы — может занять время), затем `make test-integration`
Expected: FAIL — нет `alembic.ini` / `migrations/`.

- [ ] **Step 4: Реализовать конфиг алембика и миграцию**

`alembic.ini`:

```ini
[alembic]
script_location = migrations
prepend_sys_path = .
path_separator = os
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

`migrations/env.py`:

```python
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import bot.models  # noqa: F401
from bot.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    from bot.config import get_settings

    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`migrations/script.py.mako`:

```
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

`migrations/versions/0001_initial.py`:

```python
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None

PARTITIONED = ("message_logs", "chat_member_events")


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year + 1:04d}-01-01" if month == 12 else f"{year:04d}-{month + 1:02d}-01"
    return start, end


def _create_partition(bind, parent: str, year: int, month: int) -> None:
    start, end = _month_bounds(year, month)
    bind.execute(
        sa.text(
            f"CREATE TABLE IF NOT EXISTS {parent}_{year:04d}_{month:02d} "
            f"PARTITION OF {parent} FOR VALUES FROM ('{start}') TO ('{end}')"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "chats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan", sa.String(length=16), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("show_branding", sa.Boolean(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_chats"),
        sa.UniqueConstraint("chat_id", name="uq_chats_chat_id"),
    )
    op.create_table(
        "moderators",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_moderators"),
        sa.UniqueConstraint("chat_id", "user_id", name="uq_moderators_chat_id_user_id"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_moderators_chat_id_chats", ondelete="CASCADE"
        ),
    )
    op.create_table(
        "members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("warn_count", sa.Integer(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_members"),
        sa.UniqueConstraint("chat_id", "user_id", name="uq_members_chat_id_user_id"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_members_chat_id_chats", ondelete="CASCADE"
        ),
    )
    op.create_table(
        "error_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_error_logs"),
    )
    op.create_index("ix_error_logs_created_at", "error_logs", ["created_at"])
    op.create_table(
        "daily_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("active_users", sa.Integer(), nullable=False),
        sa.Column("new_members", sa.Integer(), nullable=False),
        sa.Column("left_members", sa.Integer(), nullable=False),
        sa.Column("total_messages", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_daily_stats"),
        sa.UniqueConstraint("chat_id", "day", name="uq_daily_stats_chat_id_day"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_daily_stats_chat_id_chats", ondelete="CASCADE"
        ),
    )
    op.create_table(
        "captcha_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_captcha_challenges"),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            name="fk_captcha_challenges_chat_id_chats",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_captcha_challenges_chat_id_status", "captcha_challenges", ["chat_id", "status"]
    )
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("cron", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_scheduled_tasks"),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            name="fk_scheduled_tasks_chat_id_chats",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "paid_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.String(length=16), nullable=False),
        sa.Column("stars_amount", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("charge_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_paid_plans"),
        sa.UniqueConstraint("charge_id", name="uq_paid_plans_charge_id"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_paid_plans_chat_id_chats", ondelete="CASCADE"
        ),
    )

    op.execute(
        """
        CREATE TABLE message_logs (
            id UUID NOT NULL,
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            filter_type VARCHAR(32) NOT NULL,
            action VARCHAR(16) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT pk_message_logs PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE INDEX ix_message_logs_chat_created_at ON message_logs (chat_id, created_at)")
    op.execute(
        """
        CREATE TABLE chat_member_events (
            id UUID NOT NULL,
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            event_type VARCHAR(8) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT pk_chat_member_events PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute(
        "CREATE INDEX ix_chat_member_events_chat_created_at "
        "ON chat_member_events (chat_id, created_at)"
    )

    now = datetime.now(timezone.utc)
    months = [(now.year, now.month)]
    if now.month == 12:
        months.append((now.year + 1, 1))
    else:
        months.append((now.year, now.month + 1))
    for parent in PARTITIONED:
        for year, month in months:
            _create_partition(bind, parent, year, month)


def downgrade() -> None:
    for name in ("chat_member_events", "message_logs"):
        op.drop_table(name)
    for name in (
        "paid_plans",
        "scheduled_tasks",
        "captcha_challenges",
        "daily_stats",
        "error_logs",
        "members",
        "moderators",
        "chats",
    ):
        op.drop_table(name)
```

- [ ] **Step 5: Запустить интеграционный тест — проверить PASS**

Run: `make test-integration`
Expected: `1 passed`.
Если `command.check` сообщает о расхождении схемы (drift) — это реальное несоответствие DDL и моделей: исправляй DDL миграции, модели не трогай, пока `check` не пройдёт. Частые кандидаты: имена констрейнтов, `nullable`, типы колонок.

- [ ] **Step 6: Проверить, что юнит-тесты не сломаны**

Run: `make test`
Expected: `13 passed` (интеграционный исключён маркером).

- [ ] **Step 7: Форматирование, линт, типы**

Run: `make format && make lint && make typecheck`
Expected: без ошибок.

- [ ] **Step 8: Коммит**

```bash
git add alembic.ini migrations docker-compose.yml ops tests/test_migrations.py
git commit -m "feat: alembic initial schema with monthly partitions and compose"
```

---

### Task 5: Обработка ошибок (ErrorLog) и проверка прав

**Files:**
- Create: `bot/support/__init__.py`, `bot/support/errors.py`, `bot/support/permissions.py`
- Test: `tests/test_errors.py`, `tests/test_permissions.py`

**Interfaces:**
- Consumes: `Database.session_factory` (Task 2), модель `ErrorLog` (Task 3), aiogram `Dispatcher.update.outer_middleware`, `BaseMiddleware`.
- Produces:
  - `bot.support.errors.ErrorLogMiddleware(session_factory: async_sessionmaker[AsyncSession])` — ловит исключения из handlers, пишет строку в `ErrorLog`, глотает ошибку (update-цикл не падает), возвращает `None`.
  - `bot.support.errors.register_error_handlers(dp: Dispatcher, session_factory) -> None` — регистрирует middleware на `dp.update`.
  - `bot.support.errors.record_error(session_factory, exc, *, source, chat_id=None, user_id=None) -> None`
  - `bot.support.errors.extract_chat_user(event) -> tuple[int | None, int | None]`
  - `bot.support.permissions.is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool` — `False` при `TelegramAPIError`; сравнение статусов через **кортеж** (не set: hash строковых enum-ов не совпадает со hash обычных строк).
  - Ремарка для Плана 2: порядок outer-middleware = порядок регистрации; `429 Too Many Requests` будет обрабатываться кастомной `BaseSession`, а не middleware, поэтому глушение исключений здесь безопасно.

- [ ] **Step 1: Написать падающие тесты**

`tests/test_errors.py`:

```python
from datetime import UTC, datetime

from aiogram.types import Chat, Message, Update, User
from sqlalchemy import select

from bot.db.engine import create_database
from bot.db.base import Base
from bot.models import ErrorLog
from bot.support.errors import ErrorLogMiddleware, extract_chat_user, register_error_handlers


def make_update() -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-100123, type="group"),
        from_user=User(id=42, is_bot=False, first_name="Test"),
        text="hello",
    )
    return Update(update_id=1, message=message)


async def test_extract_chat_user_from_update():
    assert extract_chat_user(make_update()) == (-100123, 42)


async def test_error_is_recorded_and_swallowed():
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        middleware = ErrorLogMiddleware(db.session_factory)

        async def failing_handler(event, data):
            raise ValueError("kaboom")

        result = await middleware(failing_handler, make_update(), {})
        assert result is None

        async with db.session() as session:
            rows = (await session.execute(select(ErrorLog))).scalars().all()
        assert len(rows) == 1
        assert rows[0].error_type == "ValueError"
        assert rows[0].message == "kaboom"
        assert rows[0].chat_id == -100123
        assert rows[0].user_id == 42
        assert rows[0].source == "handler"
    finally:
        await db.dispose()


async def test_handler_result_passes_through():
    db = create_database("sqlite+aiosqlite://")
    try:
        middleware = ErrorLogMiddleware(db.session_factory)

        async def ok_handler(event, data):
            return "ok"

        assert await middleware(ok_handler, make_update(), {}) == "ok"
    finally:
        await db.dispose()


def test_register_error_handlers_on_dispatcher():
    from aiogram import Dispatcher

    db = create_database("sqlite+aiosqlite://")
    register_error_handlers(Dispatcher(), db.session_factory)
```

`tests/test_permissions.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.support.permissions import is_chat_admin


async def test_is_chat_admin_true():
    bot = AsyncMock()
    bot.get_chat_member.return_value = SimpleNamespace(status="administrator")
    assert await is_chat_admin(bot, -100, 5) is True


async def test_is_chat_admin_member_false():
    bot = AsyncMock()
    bot.get_chat_member.return_value = SimpleNamespace(status="member")
    assert await is_chat_admin(bot, -100, 5) is False


async def test_is_chat_admin_api_error_false():
    from aiogram.exceptions import TelegramAPIError

    bot = AsyncMock()
    bot.get_chat_member.side_effect = TelegramAPIError(method=SimpleNamespace(), message="boom")
    assert await is_chat_admin(bot, -100, 5) is False
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.support'`.

- [ ] **Step 3: Реализовать `bot/support/`**

`bot/support/__init__.py` — пустой.

`bot/support/errors.py`:

```python
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Dispatcher
from aiogram.types import Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.models import ErrorLog

log = logging.getLogger(__name__)


def extract_chat_user(event: Any) -> tuple[int | None, int | None]:
    obj: Any = event
    if isinstance(event, Update):
        obj = (
            event.message
            or event.edited_message
            or event.channel_post
            or event.callback_query
            or event.chat_member
            or event.my_chat_member
            or event.chat_join_request
        )
    if obj is None:
        return None, None
    chat = getattr(obj, "chat", None)
    if chat is None:
        chat = getattr(getattr(obj, "message", None), "chat", None)
    user = getattr(obj, "from_user", None) or getattr(obj, "user", None)
    return (
        chat.id if chat is not None else None,
        user.id if user is not None else None,
    )


async def record_error(
    session_factory: async_sessionmaker[AsyncSession],
    exc: BaseException,
    *,
    source: str,
    chat_id: int | None = None,
    user_id: int | None = None,
) -> None:
    async with session_factory() as session:
        session.add(
            ErrorLog(
                source=source,
                chat_id=chat_id,
                user_id=user_id,
                error_type=type(exc).__name__,
                message=str(exc)[:2000],
            )
        )
        await session.commit()


class ErrorLogMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as exc:
            chat_id, user_id = extract_chat_user(event)
            try:
                await record_error(
                    self.session_factory,
                    exc,
                    source="handler",
                    chat_id=chat_id,
                    user_id=user_id,
                )
            except Exception:
                log.exception("failed to persist error to ErrorLog")
            log.exception("unhandled error while processing update")
            return None


def register_error_handlers(
    dp: Dispatcher, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    dp.update.outer_middleware(ErrorLogMiddleware(session_factory))
```

`bot/support/permissions.py`:

```python
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError

ADMIN_STATUSES = (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return False
    return member.status in ADMIN_STATUSES
```

- [ ] **Step 4: Запустить тесты — проверить PASS**

Run: `make test`
Expected: `20 passed` (13 прежних + 7 новых).

- [ ] **Step 5: Форматирование, линт, типы**

Run: `make format && make lint && make typecheck`
Expected: без ошибок.

- [ ] **Step 6: Коммит**

```bash
git add bot/support tests/test_errors.py tests/test_permissions.py
git commit -m "feat: error logging middleware and admin permission check"
```

---

### Task 6: Точка входа, `/health`, Redis, запуск

**Files:**
- Create: `bot/support/redis.py`, `bot/app.py`
- Test: `tests/test_health.py`
- Modify: `Makefile` (цель `run` уже добавлена в Task 1)

**Interfaces:**
- Consumes: `get_settings()` (Task 1), `create_database` (Task 2), `register_error_handlers` (Task 5).
- Produces:
  - `bot.support.redis.create_redis(url: str) -> Redis` (async, `decode_responses=True`).
  - `bot.app.setup_logging(level: str) -> None`
  - `bot.app.health(request: web.Request) -> web.Response` — 200 `{"status":"ok",...}` при живых DB+Redis, иначе 503 `{"status":"degraded",...}`.
  - `bot.app.build_health_app(db, redis) -> web.Application` — маршрут `GET /health`.
  - `bot.app.create_dispatcher(db: Database) -> Dispatcher` — **сюда План 2 будет добавлять роутеры модулей**.
  - `bot.app.main() -> None` — точка входа `python -m bot.app`; режим `polling` (health-сервер параллельно с `start_polling`) или `webhook` (`POST {webhook_path}` + health на одном порту, `set_webhook` при старте).
  - `bot.app.ALLOWED_UPDATES = ["message", "edited_message", "callback_query", "chat_member", "my_chat_member", "chat_join_request"]` — обязателен и в `start_polling`, и в `set_webhook` (без `chat_member` не работают приветствие/капча — спека, «Онбординг чата»).

- [ ] **Step 1: Написать падающий тест health-эндпоинта**

`tests/test_health.py`:

```python
from aiohttp.test_utils import TestClient, TestServer

from bot.app import build_health_app, create_dispatcher


class FakeDB:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.session_factory = None

    async def ping(self) -> bool:
        return self.ok


class FakeRedis:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    async def ping(self):
        if not self.ok:
            raise RuntimeError("redis down")
        return True


async def test_health_ok():
    app = build_health_app(FakeDB(True), FakeRedis())
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/health")
        assert resp.status == 200
        body = await resp.json()
        assert body == {"status": "ok", "db": True, "redis": True}
    finally:
        await client.close()


async def test_health_degraded_when_db_down():
    app = build_health_app(FakeDB(False), FakeRedis())
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/health")
        assert resp.status == 503
        body = await resp.json()
        assert body["status"] == "degraded"
        assert body["db"] is False
    finally:
        await client.close()


async def test_health_degraded_when_redis_down():
    app = build_health_app(FakeDB(True), FakeRedis(ok=False))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/health")
        assert resp.status == 503
        body = await resp.json()
        assert body["redis"] is False
    finally:
        await client.close()


def test_create_dispatcher_registers_middleware():
    dp = create_dispatcher(FakeDB(True))
    assert dp is not None
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.app'`.

- [ ] **Step 3: Реализовать redis-клиент и точку входа**

`bot/support/redis.py`:

```python
from redis.asyncio import Redis


def create_redis(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)
```

`bot/app.py`:

```python
from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update

from bot.config import get_settings
from bot.db.engine import Database, create_database
from bot.support.errors import register_error_handlers
from bot.support.redis import create_redis

log = logging.getLogger(__name__)

ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "callback_query",
    "chat_member",
    "my_chat_member",
    "chat_join_request",
]


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def health(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    redis = request.app["redis"]
    db_ok = await db.ping()
    try:
        redis_ok = bool(await redis.ping())
    except Exception:
        redis_ok = False
    ok = db_ok and redis_ok
    return web.json_response(
        {"status": "ok" if ok else "degraded", "db": db_ok, "redis": redis_ok},
        status=200 if ok else 503,
    )


def build_health_app(db: Database, redis) -> web.Application:
    app = web.Application()
    app["db"] = db
    app["redis"] = redis
    app.router.add_get("/health", health)
    return app


def create_dispatcher(db: Database) -> Dispatcher:
    dp = Dispatcher()
    register_error_handlers(dp, db.session_factory)
    return dp


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    db = create_database(settings.database_url)
    redis = create_redis(settings.redis_url)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher(db)
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


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Запустить тесты — проверить PASS**

Run: `make test`
Expected: `24 passed` (20 прежних + 4 новых).

- [ ] **Step 5: Форматирование, линт, типы**

Run: `make format && make lint && make typecheck`
Expected: без ошибок.

- [ ] **Step 6: Smoke-запуск без токена — проверить, что процесс стартует и падает с понятной ошибкой конфига**

Run: `env -u BOT_TOKEN .venv/bin/python -m bot.app`
Expected: `ValidationError` про `bot_token` (не traceback из-за импортов).

Run: `cp .env.example .env && vim .env` (заполнить BOT_TOKEN) → `make up && make migrate && make run` — вручную, когда есть реальный токен: процесс пишет `starting polling mode...`, `curl localhost:8080/health` → `{"status":"ok",...}`. Этот шаг — ручной smoke, автоматическим тестом не покрывается.

- [ ] **Step 7: Коммит**

```bash
git add bot/app.py bot/support/redis.py tests/test_health.py
git commit -m "feat: application entrypoint with health endpoint"
```

---

## Покрытие спеки этим планом

| Раздел спеки | План |
|---|---|
| Стек (Python/aiogram/Postgres/Redis/Alembic/pytest) | План 1 |
| Архитектура: структура модулей, мульти-тенантность (таблицы Chat/Moderator) | План 1 (таблицы) + План 2 (флоу) |
| Модели БД (все 10) + партиционирование + индексы | План 1 |
| Анти-спам / Приветствие / Капча | План 2 |
| Rate limits (3 уровня, 429-retry) | План 2 |
| Онбординг чата (privacy mode, member_count, abuse-limit 10, архивация) | План 2 |
| Шедулер / Статистика / worker (APScheduler, агрегация, партиции-джобы) | План 3 |
| Тарифы, Telegram Stars, renewal-реминдеры, grace period | План 3 |
| Мониторинг (Prometheus, alerting) | План 3 |
| Ошибки: ErrorLog-middleware, TelegramAPIError-retry (429), alert суперадмину | План 1 (ErrorLog) + План 2 (retry в сессии) + План 3 (alert) |
| Docker-compose (bot+worker), `make seed`, туннель/`make dev` | План 3 |
| `/health` | План 1 |
| i18n / Mini App / white-label | вне MVP (спека) |

Отклонение от спеки: локальный запуск выбирается `MODE` в env (`python -m bot.app` вместо флага `--polling`) — режим и так задаётся конфигом, отдельный CLI-флаг не плодим.

## Known risks

- Скачивание образов `postgres:16-alpine`/`redis:7-alpine` через docker.io в этой сети может быть медленным; образы тянутся заранее (`podman pull docker.io/library/postgres:16-alpine`) до `make up`.
- `alembic check` в интеграционном тесте — единственный строгий гарант совпадения DDL и моделей; при drift исправлять DDL (модели — источник истины).
