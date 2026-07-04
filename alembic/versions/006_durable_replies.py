"""Add durable pending reply queue and message reply status.

Revision ID: 006
Revises: 005
Create Date: 2026-07-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.database.migration_utils import (
    add_column_if_missing,
    column_names,
    create_index_if_missing,
    table_exists,
)

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists("pending_replies"):
        op.create_table(
            "pending_replies",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("external_event_id", sa.String(length=512), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
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
            sa.UniqueConstraint(
                "event_type",
                "external_event_id",
                name="uq_pending_replies_event",
            ),
        )
        create_index_if_missing(
            "ix_pending_replies_event_type",
            "pending_replies",
            ["event_type"],
        )
        create_index_if_missing(
            "ix_pending_replies_created_at",
            "pending_replies",
            ["created_at"],
        )

    add_column_if_missing(
        "messages",
        sa.Column("reply_status", sa.String(length=16), nullable=True),
    )
    add_column_if_missing(
        "messages",
        sa.Column("reply_error", sa.Text(), nullable=True),
    )

    if "reply_status" in column_names("messages"):
        op.execute(
            sa.text(
                "UPDATE messages SET reply_status = 'sent' "
                "WHERE direction = 'incoming' AND reply_status IS NULL"
            )
        )


def downgrade() -> None:
    from app.database.migration_utils import drop_column_if_exists, drop_table_if_exists

    drop_column_if_exists("messages", "reply_error")
    drop_column_if_exists("messages", "reply_status")
    drop_table_if_exists("pending_replies")
