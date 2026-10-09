from aiogram.types import User
from conftest import RecordingBot

from bot.modules.greeting.handler import on_member_joined


def make_chat(*, settings: dict, show_branding: bool = True):
    from types import SimpleNamespace

    return SimpleNamespace(chat_id=-100, show_branding=show_branding, settings=settings)


SETTINGS = {
    "greeting": {
        "enabled": True,
        "text": "Добро пожаловать!",
        "buttons": [{"text": "Канал", "url": "https://t.me/ch"}],
    }
}


async def test_greeting_sends_text(fake_limiter):
    bot = RecordingBot()
    await on_member_joined(
        None,
        bot,
        fake_limiter,
        make_chat(settings=SETTINGS),
        User(id=5, is_bot=False, first_name="U"),
    )
    assert bot.called("send_message") == 1
    assert bot.calls[0][1][0] == -100
    assert "Добро пожаловать!" in bot.calls[0][1][1]


async def test_greeting_renders_buttons(fake_limiter):
    bot = RecordingBot()
    await on_member_joined(
        None,
        bot,
        fake_limiter,
        make_chat(settings=SETTINGS),
        User(id=5, is_bot=False, first_name="U"),
    )
    _, _, kwargs = bot.calls[0]
    markup = kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "Канал"
    assert markup.inline_keyboard[0][0].url == "https://t.me/ch"


async def test_greeting_appends_branding(fake_limiter):
    bot = RecordingBot()
    await on_member_joined(
        None,
        bot,
        fake_limiter,
        make_chat(settings=SETTINGS, show_branding=True),
        User(id=5, is_bot=False, first_name="U"),
    )
    assert "@mod_bot" in bot.calls[0][1][1]


async def test_greeting_disabled_does_nothing(fake_limiter):
    bot = RecordingBot()
    await on_member_joined(
        None,
        bot,
        fake_limiter,
        make_chat(settings={"greeting": {"enabled": False, "text": "x", "buttons": []}}),
        User(id=5, is_bot=False, first_name="U"),
    )
    assert bot.called("send_message") == 0
