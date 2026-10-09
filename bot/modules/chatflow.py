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
