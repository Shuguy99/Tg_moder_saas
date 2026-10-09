from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | list[str] | None = None
depends_on: str | list[str] | None = None

PARTITIONED = ("message_logs", "chat_member_events")


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year + 1:04d}-01-01" if month == 12 else f"{year:04d}-{month + 1:02d}-01"
    return start, end


def _create_partition(bind, parent: str, year: int, month: int) -> None:
    start, end = _month_bounds(year, month)
    bind.execute(
        sa.text(
            f"CREATE TABLE IF NOT EXISTS {parent}_{year:04d}_{month:02d} "
            f"PARTITION OF {parent} FOR VALUES FROM ('{start}') TO ('{end}')"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "chats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan", sa.String(length=16), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("show_branding", sa.Boolean(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_chats"),
        sa.UniqueConstraint("chat_id", name="uq_chats_chat_id"),
    )
    op.create_table(
        "moderators",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_moderators"),
        sa.UniqueConstraint("chat_id", "user_id", name="uq_moderators_chat_id_user_id"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_moderators_chat_id_chats", ondelete="CASCADE"
        ),
    )
    op.create_table(
        "members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("warn_count", sa.Integer(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_members"),
        sa.UniqueConstraint("chat_id", "user_id", name="uq_members_chat_id_user_id"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_members_chat_id_chats", ondelete="CASCADE"
        ),
    )
    op.create_table(
        "error_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_error_logs"),
    )
    op.create_index("ix_error_logs_created_at", "error_logs", ["created_at"])
    op.create_table(
        "daily_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("active_users", sa.Integer(), nullable=False),
        sa.Column("new_members", sa.Integer(), nullable=False),
        sa.Column("left_members", sa.Integer(), nullable=False),
        sa.Column("total_messages", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_daily_stats"),
        sa.UniqueConstraint("chat_id", "day", name="uq_daily_stats_chat_id_day"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_daily_stats_chat_id_chats", ondelete="CASCADE"
        ),
    )
    op.create_table(
        "captcha_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_captcha_challenges"),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            name="fk_captcha_challenges_chat_id_chats",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_captcha_challenges_chat_id_status", "captcha_challenges", ["chat_id", "status"]
    )
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("cron", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_scheduled_tasks"),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            name="fk_scheduled_tasks_chat_id_chats",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "paid_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.String(length=16), nullable=False),
        sa.Column("stars_amount", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("charge_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_paid_plans"),
        sa.UniqueConstraint("charge_id", name="uq_paid_plans_charge_id"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_paid_plans_chat_id_chats", ondelete="CASCADE"
        ),
    )

    op.execute(
        """
        CREATE TABLE message_logs (
            id UUID NOT NULL,
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            filter_type VARCHAR(32) NOT NULL,
            action VARCHAR(16) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT pk_message_logs PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute("CREATE INDEX ix_message_logs_chat_created_at ON message_logs (chat_id, created_at)")
    op.execute(
        """
        CREATE TABLE chat_member_events (
            id UUID NOT NULL,
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            event_type VARCHAR(8) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT pk_chat_member_events PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute(
        "CREATE INDEX ix_chat_member_events_chat_created_at "
        "ON chat_member_events (chat_id, created_at)"
    )

    now = datetime.now(UTC)
    months = [(now.year, now.month)]
    if now.month == 12:
        months.append((now.year + 1, 1))
    else:
        months.append((now.year, now.month + 1))
    for parent in PARTITIONED:
        for year, month in months:
            _create_partition(bind, parent, year, month)


def downgrade() -> None:
    for name in ("chat_member_events", "message_logs"):
        op.drop_table(name)
    for name in (
        "paid_plans",
        "scheduled_tasks",
        "captcha_challenges",
        "daily_stats",
        "error_logs",
        "members",
        "moderators",
        "chats",
    ):
        op.drop_table(name)
