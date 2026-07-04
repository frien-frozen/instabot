"""Add multi-account automation engine tables.

Revision ID: 003
Revises: 002
Create Date: 2026-07-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.database.migration_utils import (
    create_index_if_missing,
    drop_table_if_exists,
    table_exists,
)

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists("instagram_accounts"):
        op.create_table(
            "instagram_accounts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("instagram_user_id", sa.String(length=255), nullable=False),
            sa.Column("username", sa.String(length=255), nullable=True),
            sa.Column("access_token", sa.Text(), nullable=False),
            sa.Column("graph_host", sa.String(length=255), nullable=False, server_default="graph.instagram.com"),
            sa.Column("api_version", sa.String(length=32), nullable=False, server_default="v21.0"),
            sa.Column("gemini_model", sa.String(length=128), nullable=True),
            sa.Column("system_prompt", sa.Text(), nullable=False),
            sa.Column("reply_delay_min", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("reply_delay_max", sa.Integer(), nullable=False, server_default="15"),
            sa.Column("comments_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("messages_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("mentions_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("instagram_user_id"),
        )

    create_index_if_missing("ix_instagram_accounts_active", "instagram_accounts", ["is_active"])

    if not table_exists("knowledge"):
        op.create_table(
            "knowledge",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False, server_default="custom"),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["instagram_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    create_index_if_missing("ix_knowledge_account_category", "knowledge", ["account_id", "category"])
    create_index_if_missing("ix_knowledge_account_active", "knowledge", ["account_id", "is_active"])

    if not table_exists("conversation_logs"):
        op.create_table(
            "conversation_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("platform", sa.String(length=32), nullable=False, server_default="instagram"),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("external_event_id", sa.String(length=512), nullable=True),
            sa.Column("external_user_id", sa.String(length=255), nullable=True),
            sa.Column("username", sa.String(length=255), nullable=True),
            sa.Column("incoming_text", sa.Text(), nullable=True),
            sa.Column("generated_reply", sa.Text(), nullable=True),
            sa.Column("api_status", sa.String(length=32), nullable=True),
            sa.Column("api_response", sa.Text(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["instagram_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    create_index_if_missing(
        "ix_conversation_logs_account_created",
        "conversation_logs",
        ["account_id", "created_at"],
    )
    create_index_if_missing("ix_conversation_logs_event_type", "conversation_logs", ["event_type"])
    create_index_if_missing(
        "ix_conversation_logs_external_event",
        "conversation_logs",
        ["external_event_id"],
    )

    if not table_exists("processed_events"):
        op.create_table(
            "processed_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("platform", sa.String(length=32), nullable=False, server_default="instagram"),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("external_event_id", sa.String(length=512), nullable=False),
            sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["instagram_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "account_id",
                "platform",
                "event_type",
                "external_event_id",
                name="uq_processed_events_dedupe",
            ),
        )

    create_index_if_missing("ix_processed_events_account", "processed_events", ["account_id"])


def downgrade() -> None:
    drop_table_if_exists("processed_events")
    drop_table_if_exists("conversation_logs")
    drop_table_if_exists("knowledge")
    drop_table_if_exists("instagram_accounts")
