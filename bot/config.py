from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    mode: Literal["polling", "webhook"] = "polling"
    webhook_url: str | None = None
    webhook_path: str = "/tg/webhook"
    database_url: str = "postgresql+psycopg://bot:bot@localhost:5432/botdb"
    redis_url: str = "redis://localhost:6379/0"
    superadmin_ids: str = ""
    health_port: int = 8080
    log_level: str = "INFO"
    stars_pro_price: int = 150

    @field_validator("bot_token")
    @classmethod
    def bot_token_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("bot_token must not be empty")
        return value

    @model_validator(mode="after")
    def check_webhook(self) -> Settings:
        if self.mode == "webhook" and not self.webhook_url:
            raise ValueError("webhook_url is required when mode=webhook")
        return self

    @property
    def superadmins(self) -> frozenset[int]:
        parts = [p.strip() for p in self.superadmin_ids.split(",") if p.strip()]
        return frozenset(int(p) for p in parts)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
