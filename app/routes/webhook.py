"""Instagram Meta webhook routes."""

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.config import Settings, get_settings
from app.dependencies import get_comment_processor, get_message_processor
from app.schemas import CommentCreate, InstagramWebhookPayload, MessageCreate
from app.services.comment_processor import CommentProcessor
from app.services.message_processor import MessageProcessor
from app.utils.logging import get_logger, log_event
from app.utils.webhook_logging import log_all_webhook_events

logger = get_logger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])

COMMENT_FIELDS = frozenset({"comments", "live_comments"})


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    """Meta webhook verification endpoint (GET)."""
    log_event(logger, logging.INFO, "webhook_verification_attempt", hub_mode=hub_mode)

    if hub_mode == "subscribe" and hub_verify_token == settings.verify_token:
        log_event(logger, logging.INFO, "webhook_verification_success")
        return PlainTextResponse(content=hub_challenge, status_code=200)

    log_event(logger, logging.WARNING, "webhook_verification_failed")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token mismatch",
    )


def _normalize_webhook_body(body: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Normalize bare Meta test payloads into the standard envelope."""
    if "object" in body and "entry" in body:
        return body

    if "field" in body and "value" in body:
        account_id = settings.resolved_instagram_user_id or "unknown"
        log_event(
            logger,
            logging.INFO,
            "webhook_payload_normalized",
            field=body.get("field"),
            account_id=account_id,
        )
        return {
            "object": "instagram",
            "entry": [{"id": account_id, "changes": [body]}],
        }

    return body


def _extract_comments(body: dict[str, Any]) -> list[CommentCreate]:
    """Extract comment records from a webhook body."""
    comments: list[CommentCreate] = []

    if body.get("object") != "instagram":
        return comments

    for entry in body.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        account_id = str(entry.get("id", ""))

        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            if change.get("field") not in COMMENT_FIELDS:
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                continue

            comment_id = value.get("id")
            if not comment_id:
                continue

            from_user = value.get("from") or {}
            media = value.get("media") or {}

            comments.append(
                CommentCreate(
                    comment_id=str(comment_id),
                    username=from_user.get("username", "unknown") if isinstance(from_user, dict) else "unknown",
                    message=value.get("text", "") or "",
                    media_id=str(media.get("id", "")) if isinstance(media, dict) else "",
                    from_id=str(from_user.get("id", "")) if isinstance(from_user, dict) and from_user.get("id") else None,
                    parent_comment_id=value.get("parent_id"),
                    account_id=account_id,
                )
            )

    return comments


def _extract_messages(body: dict[str, Any]) -> list[MessageCreate]:
    """Extract incoming text DM events from a webhook body."""
    messages: list[MessageCreate] = []

    if body.get("object") != "instagram":
        return messages

    for entry in body.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        account_id = str(entry.get("id", ""))

        for event in entry.get("messaging") or []:
            if not isinstance(event, dict):
                continue

            # Skip delivery, read, and reaction events
            if event.get("read") or event.get("delivery") or event.get("reaction"):
                continue

            message = event.get("message")
            if not isinstance(message, dict):
                continue

            is_echo = bool(message.get("is_echo"))

            # Text-only for now — skip attachment-only messages
            text = message.get("text")
            if not text or not str(text).strip():
                continue

            message_id = message.get("mid")
            sender = event.get("sender") or {}
            recipient = event.get("recipient") or {}

            if not message_id or not isinstance(sender, dict):
                continue

            messages.append(
                MessageCreate(
                    message_id=str(message_id),
                    sender_id=str(sender.get("id", "")),
                    recipient_id=str(recipient.get("id", "")) if isinstance(recipient, dict) else "",
                    text=str(text),
                    timestamp=event.get("timestamp"),
                    account_id=account_id,
                    is_echo=is_echo,
                )
            )

    return messages


@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    comment_processor: CommentProcessor = Depends(get_comment_processor),
    message_processor: MessageProcessor = Depends(get_message_processor),
) -> dict[str, Any]:
    """Receive Instagram webhook events (POST)."""
    raw_body = await request.body()
    headers = dict(request.headers)
    client_ip = request.client.host if request.client else "unknown"
    forwarded_for = headers.get("x-forwarded-for", "")

    logger.info("RAW BODY: %r", raw_body)
    logger.info("HEADERS: %s", headers)
    log_event(
        logger,
        logging.INFO,
        "webhook_post_received",
        client_ip=client_ip,
        forwarded_for=forwarded_for,
        content_length=len(raw_body),
        user_agent=headers.get("user-agent", ""),
    )

    if not raw_body:
        logger.warning("Empty webhook payload from %s", client_ip)
        return {"ok": False, "error": "empty_payload"}

    try:
        body: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.exception("JSON parse failed")
        return {"ok": False, "error": "invalid_json"}

    if not isinstance(body, dict):
        logger.exception("JSON parse failed: payload is not an object")
        return {"ok": False, "error": "invalid_json"}

    # Log every event in full detail BEFORE any filtering
    log_all_webhook_events(body, client_ip=client_ip)

    body = _normalize_webhook_body(body, settings)

    if body.get("object") != "instagram":
        return {"status": "ignored", "reason": "unsupported_object"}

    comments = _extract_comments(body)
    messages = _extract_messages(body)

    log_event(
        logger,
        logging.INFO,
        "webhook_dispatch",
        client_ip=client_ip,
        comment_count=len(comments),
        message_count=len(messages),
    )

    for comment_data in comments:
        background_tasks.add_task(comment_processor.process, comment_data)

    for message_data in messages:
        background_tasks.add_task(message_processor.process, message_data)

    return {
        "status": "ok",
        "comments_queued": str(len(comments)),
        "messages_queued": str(len(messages)),
    }
