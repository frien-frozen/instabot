"""Add dashboard-driven agent configuration fields.

Revision ID: 004
Revises: 003
Create Date: 2026-07-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "instagram_accounts",
        sa.Column("display_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "instagram_accounts",
        sa.Column("ai_provider", sa.String(length=32), nullable=False, server_default="gemini"),
    )
    op.add_column(
        "instagram_accounts",
        sa.Column("ai_api_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "instagram_accounts",
        sa.Column("story_mentions_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "instagram_accounts",
        sa.Column("language_mode", sa.String(length=32), nullable=False, server_default="auto"),
    )
    op.create_index(
        "ix_instagram_accounts_username",
        "instagram_accounts",
        ["username"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_instagram_accounts_username", table_name="instagram_accounts")
    op.drop_column("instagram_accounts", "language_mode")
    op.drop_column("instagram_accounts", "story_mentions_enabled")
    op.drop_column("instagram_accounts", "ai_api_key")
    op.drop_column("instagram_accounts", "ai_provider")
    op.drop_column("instagram_accounts", "display_name")
