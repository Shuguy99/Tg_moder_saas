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
