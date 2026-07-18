"""Cached Instagram media (Reels/posts) for comment context."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from beanie import Document, Indexed
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class Media(Document):
    """Cached Graph API media metadata + classified intent."""

    id: Optional[int] = None
    media_id: Indexed(str, unique=True)
    media_type: str = ""
    caption: str = ""
    permalink: str = ""
    timestamp: Optional[str] = None
    like_count: Optional[int] = None
    comments_count: Optional[int] = None
    intent: str = "education"  # lead_magnet | consultation | operation | education | ...
    campaign_id: Optional[int] = None
    raw: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "media"
        indexes = [
            IndexModel([("intent", ASCENDING)]),
            IndexModel([("fetched_at", ASCENDING)]),
        ]
