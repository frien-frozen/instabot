"""Message model — individual Instagram DM records."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from beanie import Document, Indexed
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class Message(Document):
    """Single Instagram Direct Message (incoming or outgoing)."""

    id: Optional[int] = None
    message_id: Indexed(str, unique=True)
    conversation_id: Indexed(int)
    sender_id: str
    text: str = ""
    timestamp: Optional[datetime] = None
    direction: str  # incoming | outgoing
    reply_status: Optional[str] = None
    reply_error: Optional[str] = None

    class Settings:
        name = "messages"
        indexes = [
            IndexModel([("direction", ASCENDING)]),
        ]
