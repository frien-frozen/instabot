"""Platform-agnostic event types for the automation engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class BaseEvent:
    """Common fields for all inbound platform events."""

    platform: str
    event_type: str
    account_external_id: str
    external_event_id: str


@dataclass(frozen=True)
class CommentEvent(BaseEvent):
    comment_id: str
    username: str
    text: str
    media_id: str
    from_id: Optional[str] = None
    parent_comment_id: Optional[str] = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MessageEvent(BaseEvent):
    message_id: str
    sender_id: str
    recipient_id: str
    text: str
    timestamp: Optional[int] = None
    is_echo: bool = False
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MentionEvent(BaseEvent):
    mention_id: str
    username: str
    text: str
    mention_type: str
    media_id: Optional[str] = None
    comment_id: Optional[str] = None
    from_id: Optional[str] = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
