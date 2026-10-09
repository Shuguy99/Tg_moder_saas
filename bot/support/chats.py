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
