from sqlalchemy import text

from bot.db.base import Base
from bot.db.engine import create_database


def test_base_is_declarative_with_naming_convention():
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"
    assert Base.metadata.naming_convention["fk"] == (
        "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    )


async def test_ping_and_session():
    db = create_database("sqlite+aiosqlite://")
    try:
        assert await db.ping() is True
        async with db.session() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await db.dispose()


async def test_ping_false_on_unreachable_db():
    db = create_database("postgresql+psycopg://bot:bot@127.0.0.1:1/none")
    try:
        assert await db.ping() is False
    finally:
        await db.dispose()
