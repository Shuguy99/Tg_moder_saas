from datetime import UTC, datetime

from aiogram.types import Chat, Message, Update, User
from sqlalchemy import select

from bot.db.base import Base
from bot.db.engine import create_database
from bot.models import ErrorLog
from bot.support.errors import ErrorLogMiddleware, extract_chat_user, register_error_handlers


def make_update() -> Update:
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-100123, type="group"),
        from_user=User(id=42, is_bot=False, first_name="Test"),
        text="hello",
    )
    return Update(update_id=1, message=message)


async def test_extract_chat_user_from_update():
    assert extract_chat_user(make_update()) == (-100123, 42)


async def test_error_is_recorded_and_swallowed():
    db = create_database("sqlite+aiosqlite://")
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        middleware = ErrorLogMiddleware(db.session_factory)

        async def failing_handler(event, data):
            raise ValueError("kaboom")

        result = await middleware(failing_handler, make_update(), {})
        assert result is None

        async with db.session() as session:
            rows = (await session.execute(select(ErrorLog))).scalars().all()
        assert len(rows) == 1
        assert rows[0].error_type == "ValueError"
        assert rows[0].message == "kaboom"
        assert rows[0].chat_id == -100123
        assert rows[0].user_id == 42
        assert rows[0].source == "handler"
    finally:
        await db.dispose()


async def test_handler_result_passes_through():
    db = create_database("sqlite+aiosqlite://")
    try:
        middleware = ErrorLogMiddleware(db.session_factory)

        async def ok_handler(event, data):
            return "ok"

        assert await middleware(ok_handler, make_update(), {}) == "ok"
    finally:
        await db.dispose()


def test_register_error_handlers_on_dispatcher():
    from aiogram import Dispatcher

    db = create_database("sqlite+aiosqlite://")
    register_error_handlers(Dispatcher(), db.session_factory)
