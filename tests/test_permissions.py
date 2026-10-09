from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.support.permissions import is_chat_admin


async def test_is_chat_admin_true():
    bot = AsyncMock()
    bot.get_chat_member.return_value = SimpleNamespace(status="administrator")
    assert await is_chat_admin(bot, -100, 5) is True


async def test_is_chat_admin_member_false():
    bot = AsyncMock()
    bot.get_chat_member.return_value = SimpleNamespace(status="member")
    assert await is_chat_admin(bot, -100, 5) is False


async def test_is_chat_admin_api_error_false():
    from aiogram.exceptions import TelegramAPIError

    bot = AsyncMock()
    bot.get_chat_member.side_effect = TelegramAPIError(method=SimpleNamespace(), message="boom")
    assert await is_chat_admin(bot, -100, 5) is False
