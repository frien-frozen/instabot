"""Incoming Instagram event queue."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EventStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class EventType:
    DM = "dm"
    COMMENT = "comment"
    MENTION = "mention"
    STORY_MENTION = "story_mention"


class Event(Base):
    """Queued webhook event processed asynchronously by workers."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(512), nullable=False)
    sender_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    recipient_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=EventStatus.PENDING, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_events_event_id"),
        Index("ix_events_status_retry", "status", "next_retry_at"),
        Index("ix_events_created_at", "created_at"),
    )
