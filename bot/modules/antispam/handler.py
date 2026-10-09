from __future__ import annotations

import hashlib

from aiogram import Bot, F, Router
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
