"""Campaign definitions for comment CTA → public reply + DM flows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class Campaign(Document):
    """
    Predefined post/campaign automation.

    When a comment matches trigger_keywords (optionally scoped to media_id),
    the bot uses public_reply + dm_text instead of free-form Gemini guessing.
    """

    id: Optional[int] = None
    name: str
    enabled: bool = True
    media_id: Optional[str] = None  # None = any media; matched by trigger keywords
    goal: str = "lead_magnet"
    intent: str = "lead_magnet"
    trigger_keywords: list[str] = Field(default_factory=list)
    public_reply: str = ""
    dm_text: str = ""
    dm_attachment_url: Optional[str] = None  # hosted PDF/guide URL included in DM if set
    ask_name_after_dm: bool = True
    ask_phone_after_dm: bool = True
    offer_consultation: bool = True
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "campaigns"
        indexes = [
            IndexModel([("enabled", ASCENDING), ("media_id", ASCENDING)]),
        ]
