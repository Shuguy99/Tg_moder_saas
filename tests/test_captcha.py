from datetime import UTC, datetime

from aiogram.types import CallbackQuery, User
from conftest import RecordingBot
from sqlalchemy import select

from bot.db.base import Base
from bot.db.engine import create_database
from bot.models import CaptchaChallenge, CaptchaStatus
from bot.modules.captcha.handler import on_callback, on_member_joined
from bot.support.chats import get_or_create_chat


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
        chat.settings["captcha"]["enabled"] = False
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
