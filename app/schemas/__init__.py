"""Pydantic v2 schemas for request/response validation."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WebhookVerificationParams(BaseModel):
    """Query parameters for Meta webhook verification (GET)."""

    hub_mode: str = Field(alias="hub.mode")
    hub_verify_token: str = Field(alias="hub.verify_token")
    hub_challenge: str = Field(alias="hub.challenge")

    model_config = ConfigDict(populate_by_name=True)


class InstagramUser(BaseModel):
    """Instagram user embedded in webhook payloads."""

    id: str | None = None
    username: str | None = None


class InstagramMedia(BaseModel):
    """Media reference embedded in comment webhook payloads."""

    id: str | None = None
    media_product_type: str | None = None


class InstagramCommentValue(BaseModel):
    """Comment data from an Instagram webhook change event."""

    id: str
    text: str | None = None
    from_user: InstagramUser | None = Field(default=None, alias="from")
    media: InstagramMedia | None = None
    parent_id: str | None = None
    timestamp: int | None = None

    model_config = ConfigDict(populate_by_name=True)


class InstagramChange(BaseModel):
    """Single change object within a webhook entry."""

    field: str
    value: dict | InstagramCommentValue


class InstagramEntry(BaseModel):
    """Webhook entry containing one or more change events."""

    id: str
    time: int | None = None
    changes: list[InstagramChange] | None = None
    messaging: list[dict] | None = None


class InstagramWebhookPayload(BaseModel):
    """Top-level Instagram webhook POST body."""

    object: str
    entry: list[InstagramEntry] = Field(default_factory=list)


class CommentCreate(BaseModel):
    """Internal schema for persisting a new comment."""

    comment_id: str
    username: str
    message: str
    media_id: str
    parent_comment_id: str | None = None
    account_id: str | None = None


class CommentResponse(BaseModel):
    """API response schema for a stored comment."""

    id: int
    comment_id: str
    username: str
    message: str
    media_id: str
    parent_comment_id: str | None
    replied: bool
    reply_text: str | None
    created_at: datetime
    replied_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    app_name: str
    environment: str
