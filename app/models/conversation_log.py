"""Audit log for every automation event and API outcome."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConversationLog(Base):
    """Persistent log of incoming events, generated replies, and API results."""

    __tablename__ = "conversation_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("instagram_accounts.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="instagram")
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    external_event_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    external_user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    incoming_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generated_reply: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    api_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    api_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_conversation_logs_account_created", "account_id", "created_at"),
        Index("ix_conversation_logs_event_type", "event_type"),
        Index("ix_conversation_logs_external_event", "external_event_id"),
    )
