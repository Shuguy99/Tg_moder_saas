from datetime import UTC, datetime

from aiogram.types import Chat as TgChat
from aiogram.types import ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, User
from conftest import RecordingBot
from sqlalchemy import select

from bot.db.base import Base
from bot.db.engine import create_database
from bot.models import Chat, Moderator
from bot.modules.chatflow import on_chat_member, router


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
