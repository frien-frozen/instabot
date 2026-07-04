"""Initial schema: comments and settings tables.

Revision ID: 001
Revises:
Create Date: 2026-07-03
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

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists("comments"):
        op.create_table(
            "comments",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("comment_id", sa.String(length=255), nullable=False),
            sa.Column("username", sa.String(length=255), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("media_id", sa.String(length=255), nullable=False),
            sa.Column("parent_comment_id", sa.String(length=255), nullable=True),
            sa.Column("replied", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("reply_text", sa.Text(), nullable=True),
            sa.Column("account_id", sa.String(length=255), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("comment_id"),
        )

    create_index_if_missing("ix_comments_media_id", "comments", ["media_id"])
    create_index_if_missing("ix_comments_replied", "comments", ["replied"])
    create_index_if_missing("ix_comments_created_at", "comments", ["created_at"])

    if not table_exists("settings"):
        op.create_table(
            "settings",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("key", sa.String(length=255), nullable=False),
            sa.Column("value", sa.Text(), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("account_id", sa.String(length=255), nullable=True),
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
            sa.UniqueConstraint("key"),
        )


def downgrade() -> None:
    drop_table_if_exists("settings")
    drop_index_if_exists("ix_comments_created_at", "comments")
    drop_index_if_exists("ix_comments_replied", "comments")
    drop_index_if_exists("ix_comments_media_id", "comments")
    drop_table_if_exists("comments")
