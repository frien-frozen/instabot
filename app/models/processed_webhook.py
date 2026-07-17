"""Single-account webhook event deduplication."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class ProcessedWebhook(Document):
    """Track processed webhook events to prevent duplicate replies."""

    id: Optional[int] = None
    event_type: str
    external_event_id: str
    processed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "processed_webhooks"
        indexes = [
            IndexModel(
                [("event_type", ASCENDING), ("external_event_id", ASCENDING)],
                unique=True,
            ),
        ]
