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
