"""Comment model — stores every incoming Instagram comment and reply state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from beanie import Document, Indexed
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class Comment(Document):
    """
    Persisted Instagram comment record.

    Designed for future multi-account support via optional account_id column.
    """

    id: Optional[int] = None
    comment_id: Indexed(str, unique=True)
    username: str = "unknown"
    message: str
    media_id: str = ""
    from_id: Optional[str] = None
    parent_comment_id: Optional[str] = None
    replied: bool = False
    reply_text: Optional[str] = None
    account_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    replied_at: Optional[datetime] = None

    class Settings:
        name = "comments"
        indexes = [
            IndexModel([("media_id", ASCENDING)]),
            IndexModel([("from_id", ASCENDING)]),
            IndexModel([("replied", ASCENDING)]),
            IndexModel([("created_at", ASCENDING)]),
        ]

    def __repr__(self) -> str:
        return f"<Comment id={self.id} comment_id={self.comment_id!r} replied={self.replied}>"
