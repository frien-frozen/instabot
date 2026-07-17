"""Incoming Instagram event queue."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from beanie import Document, Indexed
from pydantic import Field
from pymongo import ASCENDING, IndexModel


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


class Event(Document):
    """Queued webhook event processed asynchronously by workers."""

    id: Optional[int] = None
    event_type: Indexed(str)
    event_id: Indexed(str, unique=True)
    sender_id: Optional[str] = None
    recipient_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = EventStatus.PENDING
    attempts: int = 0
    task_id: Optional[int] = None
    last_error: Optional[str] = None
    next_retry_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    processed_at: Optional[datetime] = None

    class Settings:
        name = "events"
        indexes = [
            IndexModel([("status", ASCENDING), ("next_retry_at", ASCENDING)]),
            IndexModel([("created_at", ASCENDING)]),
        ]
