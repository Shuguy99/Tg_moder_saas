import pytest

from bot.config import get_settings


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:TEST")
    monkeypatch.setenv("MODE", "polling")
    monkeypatch.setenv("SUPERADMIN_IDS", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
