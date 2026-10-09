from pydantic import ValidationError

from bot.config import Settings


def test_defaults(monkeypatch):
    s = Settings()
    assert s.mode == "polling"
    assert s.database_url.startswith("postgresql+psycopg://")
    assert s.redis_url.startswith("redis://")
    assert s.health_port == 8080
    assert s.stars_pro_price == 150
    assert s.superadmins == frozenset()


def test_superadmins_parsing(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_IDS", "10, 20,30")
    s = Settings()
    assert s.superadmins == frozenset({10, 20, 30})


def test_webhook_mode_requires_url(monkeypatch):
    monkeypatch.setenv("MODE", "webhook")
    monkeypatch.setenv("WEBHOOK_URL", "")
    try:
        Settings()
    except ValidationError:
        pass
    else:
        raise AssertionError("webhook mode without WEBHOOK_URL must fail")


def test_webhook_mode_with_url_ok(monkeypatch):
    monkeypatch.setenv("MODE", "webhook")
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com")
    s = Settings()
    assert s.webhook_path == "/tg/webhook"
