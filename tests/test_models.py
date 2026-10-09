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
