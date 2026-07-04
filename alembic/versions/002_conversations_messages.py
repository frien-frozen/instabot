"""Add conversations and messages tables for Instagram DMs.

Revision ID: 002
Revises: 001
Create Date: 2026-07-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.database.migration_utils import (
    create_index_if_missing,
    drop_index_if_exists,
    drop_table_if_exists,
    table_exists,
)

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists("conversations"):
        op.create_table(
            "conversations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.String(length=255), nullable=False),
            sa.Column("username", sa.String(length=255), nullable=True),
            sa.Column("last_message", sa.Text(), nullable=True),
            sa.Column("account_id", sa.String(length=255), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    create_index_if_missing("ix_conversations_user_id", "conversations", ["user_id"])
    create_index_if_missing(
        "ix_conversations_account_user",
        "conversations",
        ["account_id", "user_id"],
        unique=True,
    )

    if not table_exists("messages"):
        op.create_table(
            "messages",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("message_id", sa.String(length=512), nullable=False),
            sa.Column("conversation_id", sa.Integer(), nullable=False),
            sa.Column("sender_id", sa.String(length=255), nullable=False),
            sa.Column("text", sa.Text(), nullable=False, server_default=""),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
            sa.Column("direction", sa.String(length=16), nullable=False),
            sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("message_id"),
        )

    create_index_if_missing("ix_messages_conversation_id", "messages", ["conversation_id"])
    create_index_if_missing("ix_messages_direction", "messages", ["direction"])


def downgrade() -> None:
    drop_index_if_exists("ix_messages_direction", "messages")
    drop_index_if_exists("ix_messages_conversation_id", "messages")
    drop_table_if_exists("messages")
    drop_index_if_exists("ix_conversations_account_user", "conversations")
    drop_index_if_exists("ix_conversations_user_id", "conversations")
    drop_table_if_exists("conversations")
