from __future__ import annotations

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.models import Chat
from bot.support.ratelimit import RateLimiter, throttle


async def on_member_joined(
    session_factory: async_sessionmaker[AsyncSession] | None,
    bot: Bot,
    limiter: RateLimiter,
    chat: Chat,
    user: User,
) -> None:
    cfg = chat.settings.get("greeting", {})
    if not cfg.get("enabled", False):
        return
    text = cfg.get("text", "")
    if not text:
        return
    buttons = [
        [InlineKeyboardButton(text=item["text"], url=item["url"])]
        for item in cfg.get("buttons", [])
        if item.get("text") and item.get("url")
    ]
    if chat.show_branding:
        username = (await bot.me()).username or "bot"
        text = f"{text}\n\nРаботает на базе @{username}"
    markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    await throttle(limiter, key=f"rl:bcast:{chat.chat_id}", limit=1, window=60)
    await bot.send_message(chat.chat_id, text, reply_markup=markup)
