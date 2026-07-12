"""Add automation platform tasks and events queue.

Revision ID: 007
Revises: 006
Create Date: 2026-07-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.database.migration_utils import create_index_if_missing, table_exists

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists("tasks"):
        op.create_table(
            "tasks",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("task_type", sa.String(length=64), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        create_index_if_missing("ix_tasks_task_type", "tasks", ["task_type"])

    if not table_exists("events"):
        op.create_table(
            "events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("event_id", sa.String(length=512), nullable=False),
            sa.Column("sender_id", sa.String(length=255), nullable=True),
            sa.Column("recipient_id", sa.String(length=255), nullable=True),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id", name="uq_events_event_id"),
        )
        create_index_if_missing("ix_events_event_type", "events", ["event_type"])
        create_index_if_missing("ix_events_status_retry", "events", ["status", "next_retry_at"])
        create_index_if_missing("ix_events_created_at", "events", ["created_at"])


def downgrade() -> None:
    from app.database.migration_utils import drop_table_if_exists

    drop_table_if_exists("events")
    drop_table_if_exists("tasks")
