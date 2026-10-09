from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from aiohttp import web

from bot.config import get_settings
from bot.db.engine import Database, create_database
from bot.support.errors import register_error_handlers
from bot.support.redis import Redis, create_redis

log = logging.getLogger(__name__)

DB_KEY = web.AppKey("db", Database)
REDIS_KEY = web.AppKey("redis", Redis)

ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "callback_query",
    "chat_member",
    "my_chat_member",
    "chat_join_request",
]


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def health(request: web.Request) -> web.Response:
    db = request.app[DB_KEY]
    redis = request.app[REDIS_KEY]
    db_ok = await db.ping()
    try:
        redis_ok = bool(await redis.ping())
    except Exception:
        redis_ok = False
    ok = db_ok and redis_ok
    return web.json_response(
        {"status": "ok" if ok else "degraded", "db": db_ok, "redis": redis_ok},
        status=200 if ok else 503,
    )


def build_health_app(db: Database, redis: Redis) -> web.Application:
    app = web.Application()
    app[DB_KEY] = db
    app[REDIS_KEY] = redis
    app.router.add_get("/health", health)
    return app


def create_dispatcher(db: Database) -> Dispatcher:
    dp = Dispatcher()
    register_error_handlers(dp, db.session_factory)
    return dp


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    db = create_database(settings.database_url)
    redis = create_redis(settings.redis_url)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher(db)
    runner: web.AppRunner | None = None
    try:
        if settings.mode == "polling":
            runner = web.AppRunner(build_health_app(db, redis))
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", settings.health_port).start()
            log.info("starting polling mode, health on :%s/health", settings.health_port)
            await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
        else:
            app = build_health_app(db, redis)

            async def webhook_handler(request: web.Request) -> web.Response:
                update = Update.model_validate(await request.json())
                await dp.feed_update(bot=bot, update=update)
                return web.Response(text="ok")

            app.router.add_post(settings.webhook_path, webhook_handler)
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", settings.health_port).start()
            await bot.set_webhook(
                url=f"{settings.webhook_url}{settings.webhook_path}",
                allowed_updates=ALLOWED_UPDATES,
                drop_pending_updates=True,
            )
            log.info("webhook mode, listening on :%s", settings.health_port)
            await asyncio.Event().wait()
    finally:
        if runner is not None:
            await runner.cleanup()
        await bot.session.close()
        await redis.aclose()
        await db.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
