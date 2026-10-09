from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from aiogram import Bot, F, Router
from aiogram.types import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    User,
)
from sqlalchemy import CursorResult, select, update
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
    data_str = callback_query.data or ""
    try:
        challenge_id = int(data_str.split(":", 1)[1])
    except (ValueError, IndexError):
        await bot.answer_callback_query(callback_query.id)
        return
    user_id = callback_query.from_user.id
    async with session_factory() as session:
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(CaptchaChallenge)
                .where(
                    CaptchaChallenge.id == challenge_id,
                    CaptchaChallenge.status == CaptchaStatus.PENDING.value,
                    CaptchaChallenge.user_id == user_id,
                )
                .values(status=CaptchaStatus.DONE.value)
            ),
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
