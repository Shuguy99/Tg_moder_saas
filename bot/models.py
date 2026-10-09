from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.base import Base

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Plan(enum.StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class FilterType(enum.StrEnum):
    LINK = "link"
    CAPS = "caps"
    REPEAT = "repeat"
    STOPWORD = "stopword"


class LogAction(enum.StrEnum):
    DELETE = "delete"
    WARN = "warn"
    BAN = "ban"


class CaptchaStatus(enum.StrEnum):
    PENDING = "pending"
    DONE = "done"
    EXPIRED = "expired"


class MemberEvent(enum.StrEnum):
    JOIN = "join"
    LEAVE = "leave"


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(String(255))
    settings: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    plan: Mapped[str] = mapped_column(String(16), default=Plan.FREE.value)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    show_branding: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_chats"),
        UniqueConstraint("chat_id", name="uq_chats_chat_id"),
    )


class Moderator(Base):
    __tablename__ = "moderators"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_moderators_chat_id_chats")
    )
    user_id: Mapped[int] = mapped_column(BigInteger)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_moderators"),
        UniqueConstraint("chat_id", "user_id", name="uq_moderators_chat_id_user_id"),
    )


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_members_chat_id_chats")
    )
    user_id: Mapped[int] = mapped_column(BigInteger)
    warn_count: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_members"),
        UniqueConstraint("chat_id", "user_id", name="uq_members_chat_id_user_id"),
    )


class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    filter_type: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_message_logs"),
        Index("ix_message_logs_chat_created_at", "chat_id", "created_at"),
    )


class ChatMemberEvents(Base):
    __tablename__ = "chat_member_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_chat_member_events"),
        Index("ix_chat_member_events_chat_created_at", "chat_id", "created_at"),
    )


class DailyStats(Base):
    __tablename__ = "daily_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_daily_stats_chat_id_chats")
    )
    day: Mapped[date] = mapped_column(Date)
    active_users: Mapped[int] = mapped_column(Integer, default=0)
    new_members: Mapped[int] = mapped_column(Integer, default=0)
    left_members: Mapped[int] = mapped_column(Integer, default=0)
    total_messages: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_daily_stats"),
        UniqueConstraint("chat_id", "day", name="uq_daily_stats_chat_id_day"),
    )


class CaptchaChallenge(Base):
    __tablename__ = "captcha_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_captcha_challenges_chat_id_chats")
    )
    user_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default=CaptchaStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_captcha_challenges"),
        Index("ix_captcha_challenges_chat_id_status", "chat_id", "status"),
    )


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_scheduled_tasks_chat_id_chats")
    )
    cron: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (PrimaryKeyConstraint("id", name="pk_scheduled_tasks"),)


class PaidPlan(Base):
    __tablename__ = "paid_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE", name="fk_paid_plans_chat_id_chats")
    )
    plan: Mapped[str] = mapped_column(String(16))
    stars_amount: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    charge_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_paid_plans"),
        UniqueConstraint("charge_id", name="uq_paid_plans_charge_id"),
    )


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    error_type: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_error_logs"),
        Index("ix_error_logs_created_at", "created_at"),
    )
