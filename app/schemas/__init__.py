"""Pydantic v2 schemas for request/response validation."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class WebhookVerificationParams(BaseModel):
    """Query parameters for Meta webhook verification (GET)."""

    hub_mode: str = Field(alias="hub.mode")
    hub_verify_token: str = Field(alias="hub.verify_token")
    hub_challenge: str = Field(alias="hub.challenge")

    model_config = ConfigDict(populate_by_name=True)


class InstagramUser(BaseModel):
    """Instagram user embedded in webhook payloads."""

    id: Optional[str] = None
    username: Optional[str] = None


class InstagramMedia(BaseModel):
    """Media reference embedded in comment webhook payloads."""

    id: Optional[str] = None
    media_product_type: Optional[str] = None


class InstagramCommentValue(BaseModel):
    """Comment data from an Instagram webhook change event."""

    id: str
    text: Optional[str] = None
    from_user: Optional[InstagramUser] = Field(default=None, alias="from")
    media: Optional[InstagramMedia] = None
    parent_id: Optional[str] = None
    timestamp: Optional[int] = None

    model_config = ConfigDict(populate_by_name=True)


class InstagramChange(BaseModel):
    """Single change object within a webhook entry."""

    field: str
    value: Union[dict, InstagramCommentValue]


class InstagramEntry(BaseModel):
    """Webhook entry containing one or more change events."""

    id: str
    time: Optional[int] = None
    changes: Optional[List[InstagramChange]] = None
    messaging: Optional[List[dict]] = None


class InstagramWebhookPayload(BaseModel):
    """Top-level Instagram webhook POST body."""

    object: str
    entry: List[InstagramEntry] = Field(default_factory=list)


class CommentCreate(BaseModel):
    """Internal schema for persisting a new comment."""

    comment_id: str
    username: str
    message: str
    media_id: str
    from_id: Optional[str] = None
    parent_comment_id: Optional[str] = None
    account_id: Optional[str] = None


class MessageCreate(BaseModel):
    """Internal schema for an incoming Instagram Direct Message."""

    message_id: str
    sender_id: str
    recipient_id: str
    text: str
    timestamp: Optional[int] = None
    account_id: Optional[str] = None
    is_echo: bool = False


class MentionCreate(BaseModel):
    """Internal schema for an incoming Instagram mention."""

    mention_id: str
    mention_type: str
    username: str = "unknown"
    text: str = ""
    comment_id: Optional[str] = None
    from_id: Optional[str] = None
    media_id: Optional[str] = None
    account_id: Optional[str] = None


class CommentResponse(BaseModel):
    """API response schema for a stored comment."""

    id: int
    comment_id: str
    username: str
    message: str
    media_id: str
    parent_comment_id: Optional[str]
    replied: bool
    reply_text: Optional[str]
    created_at: datetime
    replied_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    app_name: str
    environment: str


class InstagramHealthResponse(BaseModel):
    """Instagram token validation status."""

    status: str
    graph_host: str
    username: Optional[str] = None
    user_id: Optional[str] = None
    error: Optional[str] = None


class MessagesHealthResponse(BaseModel):
    """Instagram messaging capability status."""

    status: str
    graph_host: str
    messaging_webhook_enabled: bool
    access_token_valid: bool
    authenticated_user_id: Optional[str] = None
    username: Optional[str] = None
    permissions_note: Optional[str] = None
    error: Optional[str] = None


class GeminiHealthResponse(BaseModel):
    """Gemini model validation status."""

    status: str
    model: str
    test_reply: Optional[str] = None
    recommended_models: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    hint: Optional[str] = None
