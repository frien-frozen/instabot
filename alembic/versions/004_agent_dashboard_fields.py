"""Add dashboard-driven agent configuration fields.

Revision ID: 004
Revises: 003
Create Date: 2026-07-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.database.migration_utils import (
    add_column_if_missing,
    create_index_if_missing,
    drop_column_if_exists,
    drop_index_if_exists,
)

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "instagram_accounts",
        sa.Column("display_name", sa.String(length=255), nullable=True),
    )
    add_column_if_missing(
        "instagram_accounts",
        sa.Column("ai_provider", sa.String(length=32), nullable=False, server_default="gemini"),
    )
    add_column_if_missing(
        "instagram_accounts",
        sa.Column("ai_api_key", sa.Text(), nullable=True),
    )
    add_column_if_missing(
        "instagram_accounts",
        sa.Column(
            "story_mentions_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    add_column_if_missing(
        "instagram_accounts",
        sa.Column("language_mode", sa.String(length=32), nullable=False, server_default="auto"),
    )
    create_index_if_missing(
        "ix_instagram_accounts_username",
        "instagram_accounts",
        ["username"],
    )


def downgrade() -> None:
    drop_index_if_exists("ix_instagram_accounts_username", "instagram_accounts")
    drop_column_if_exists("instagram_accounts", "language_mode")
    drop_column_if_exists("instagram_accounts", "story_mentions_enabled")
    drop_column_if_exists("instagram_accounts", "ai_api_key")
    drop_column_if_exists("instagram_accounts", "ai_provider")
    drop_column_if_exists("instagram_accounts", "display_name")
