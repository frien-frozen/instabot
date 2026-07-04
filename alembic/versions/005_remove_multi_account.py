"""Remove multi-account tables and add single-account webhook dedup.

Revision ID: 005
Revises: 004
Create Date: 2026-07-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.database.migration_utils import drop_table_if_exists, table_exists

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table_name in (
        "processed_events",
        "conversation_logs",
        "knowledge",
        "instagram_accounts",
    ):
        drop_table_if_exists(table_name)

    if not table_exists("processed_webhooks"):
        op.create_table(
            "processed_webhooks",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("external_event_id", sa.String(length=512), nullable=False),
            sa.Column(
                "processed_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "event_type",
                "external_event_id",
                name="uq_processed_webhooks_dedupe",
            ),
        )


def downgrade() -> None:
    drop_table_if_exists("processed_webhooks")
