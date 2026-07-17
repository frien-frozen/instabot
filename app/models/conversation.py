"""Conversation model — one row per Instagram DM thread."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie import Document, Indexed
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class Conversation(Document):
    """Instagram Direct Message conversation thread."""

    id: Optional[int] = None
    user_id: Indexed(str)
    username: Optional[str] = None
    last_message: Optional[str] = None
    account_id: Optional[str] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "conversations"
        indexes = [
            IndexModel(
                [("account_id", ASCENDING), ("user_id", ASCENDING)],
                unique=True,
            ),
        ]
