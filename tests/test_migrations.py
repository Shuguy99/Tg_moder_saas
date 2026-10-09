import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.integration


@pytest.mark.skipif("TEST_POSTGRES_URL" not in os.environ, reason="TEST_POSTGRES_URL is not set")
def test_upgrade_schema_check_and_partition_insert(monkeypatch):
    url = os.environ["TEST_POSTGRES_URL"]
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    command.check(cfg)

    async def roundtrip() -> tuple[str, int]:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from bot.models import Chat, MessageLog

        eng = create_async_engine(url)
        async with async_sessionmaker(eng, expire_on_commit=False)() as session:
            chat = Chat(chat_id=-100999, title="integration")
            session.add(chat)
            await session.flush()
            session.add(
                MessageLog(
                    chat_id=chat.chat_id,
                    user_id=1,
                    filter_type="link",
                    action="delete",
                )
            )
            await session.commit()
        async with eng.connect() as conn:
            tableoid = (
                await conn.execute(text("SELECT tableoid::regclass::text FROM message_logs"))
            ).scalar_one()
            count = (await conn.execute(text("SELECT count(*) FROM message_logs"))).scalar_one()
        await eng.dispose()
        return tableoid, count

    tableoid, count = asyncio.run(roundtrip())
    assert count == 1
    assert tableoid.startswith("message_logs_")
