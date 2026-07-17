"""Pending reply queue for crash-safe event processing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie import Document, Indexed
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class PendingReply(Document):
    """Event waiting for an AI reply after a transient failure or crash."""

    id: Optional[int] = None
    event_type: Indexed(str)
    external_event_id: str
    payload: str
    attempts: int = 0
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "pending_replies"
        indexes = [
            IndexModel(
                [("event_type", ASCENDING), ("external_event_id", ASCENDING)],
                unique=True,
            ),
            IndexModel([("created_at", ASCENDING)]),
        ]
