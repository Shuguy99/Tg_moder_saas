from datetime import UTC, datetime

from aiogram.types import Chat, Message, User
from conftest import RecordingBot
from sqlalchemy import select

from bot.db.base import Base
from bot.db.engine import create_database
from bot.models import Member, MessageLog
from bot.modules.antispam.handler import on_message
from bot.support.chats import default_settings, get_or_create_chat


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
