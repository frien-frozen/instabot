"""Automation task definitions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from beanie import Document, Indexed
from pydantic import Field


class TaskType:
    DM_AUTO_REPLY = "dm_auto_reply"
    COMMENT_AUTO_REPLY = "comment_auto_reply"
    MENTION_REPLY = "mention_reply"
    REEL_ENGAGEMENT = "reel_engagement"


class Task(Document):
    """Configurable Instagram automation."""

    id: Optional[int] = None
    name: str
    task_type: Indexed(str)
    enabled: bool = True
    priority: int = 100
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "tasks"
