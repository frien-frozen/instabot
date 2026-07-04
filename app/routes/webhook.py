"""Instagram Meta webhook routes."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.config import Settings, get_settings
from app.dependencies import get_comment_processor, get_message_processor, get_mention_processor
from app.schemas import CommentCreate, MentionCreate, MessageCreate
from app.services.comment_processor import CommentProcessor
from app.services.mention_processor import MentionProcessor
from app.services.message_processor import MessageProcessor
from app.utils.logging import get_logger, log_event
from app.utils.webhook_logging import log_all_webhook_events

logger = get_logger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])

COMMENT_FIELDS = frozenset({"comments", "live_comments"})
MESSAGE_FIELDS = frozenset({"messages", "messaging"})
MENTION_FIELDS = frozenset({"mentions", "story_mentions"})


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


def _parse_message_timestamp(raw: Any) -> int | None:
    """Normalize Instagram timestamps (seconds or milliseconds, str or int)."""
    if raw is None:
        return None
    if isinstance(raw, str) and raw.isdigit():
        raw = int(raw)
    if isinstance(raw, (int, float)):
        value = int(raw)
        # Values below ~year 2286 in seconds are seconds; otherwise milliseconds
        if value < 10_000_000_000:
            return value * 1000
        return value
    return None


def _resolve_account_id(entry_id: str, settings: Settings) -> str:
    """Use configured Instagram account ID when webhook entry id is a placeholder."""
    if entry_id and entry_id not in ("0", "unknown"):
        return entry_id
    return settings.resolved_instagram_user_id or entry_id or "unknown"


def _message_from_payload(
    *,
    account_id: str,
    sender: Any,
    recipient: Any,
    message: Any,
    timestamp: Any,
) -> MessageCreate | None:
    """Build MessageCreate from a messaging event or messages change value."""
    if not isinstance(message, dict):
        return None

    message_id = message.get("mid")
    if not message_id or not isinstance(sender, dict):
        return None

    is_echo = bool(message.get("is_echo"))
    if is_echo:
        return None

    text = message.get("text")
    if not text or not str(text).strip():
        return None

    return MessageCreate(
        message_id=str(message_id),
        sender_id=str(sender.get("id", "")),
        recipient_id=str(recipient.get("id", "")) if isinstance(recipient, dict) else "",
        text=str(text),
        timestamp=_parse_message_timestamp(timestamp),
        account_id=account_id,
        is_echo=is_echo,
    )


def _story_mention_from_messaging(
    *,
    account_id: str,
    event: dict[str, Any],
) -> MentionCreate | None:
    """Extract story mention events delivered via messaging webhooks."""
    referral = event.get("referral")
    if not isinstance(referral, dict):
        return None

    source = str(referral.get("source", "")).upper()
    if source != "STORY_MENTION":
        return None

    sender = event.get("sender") or {}
    if not isinstance(sender, dict) or not sender.get("id"):
        return None

    message = event.get("message") or {}
    story = referral.get("story") or {}
    mention_id = (
        message.get("mid")
        or story.get("id")
        or f"story_{sender.get('id')}_{event.get('timestamp', '')}"
    )

    return MentionCreate(
        mention_id=str(mention_id),
        mention_type="story_mentions",
        username=str(sender.get("username", "unknown")),
        text=str(message.get("text", "") or "Story mention"),
        from_id=str(sender.get("id")),
        media_id=str(story.get("id")) if isinstance(story, dict) and story.get("id") else None,
        account_id=account_id,
    )


def _extract_messages(body: dict[str, Any], settings: Settings) -> list[MessageCreate]:
    """Extract incoming text DM events from a webhook body."""
    messages: list[MessageCreate] = []

    if body.get("object") != "instagram":
        return messages

    for entry in body.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        account_id = _resolve_account_id(str(entry.get("id", "")), settings)

        # Format A: entry.messaging[] (Messenger-style envelope)
        for event in entry.get("messaging") or []:
            if not isinstance(event, dict):
                continue

            if _story_mention_from_messaging(account_id=account_id, event=event) is not None:
                continue

            if event.get("read") or event.get("delivery") or event.get("reaction"):
                continue

            msg = _message_from_payload(
                account_id=account_id,
                sender=event.get("sender"),
                recipient=event.get("recipient"),
                message=event.get("message"),
                timestamp=event.get("timestamp"),
            )
            if msg is not None:
                messages.append(msg)

        # Format B: entry.changes[] with field=messages (Meta test + some IG payloads)
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            if change.get("field") not in MESSAGE_FIELDS:
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                continue

            msg = _message_from_payload(
                account_id=account_id,
                sender=value.get("sender"),
                recipient=value.get("recipient"),
                message=value.get("message"),
                timestamp=value.get("timestamp"),
            )
            if msg is not None:
                messages.append(msg)

    return messages


def _extract_mentions(body: dict[str, Any], settings: Settings) -> list[MentionCreate]:
    """Extract mention records from a webhook body."""
    mentions: list[MentionCreate] = []

    if body.get("object") != "instagram":
        return mentions

    for entry in body.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        account_id = _resolve_account_id(str(entry.get("id", "")), settings)

        for event in entry.get("messaging") or []:
            if not isinstance(event, dict):
                continue
            story_mention = _story_mention_from_messaging(account_id=account_id, event=event)
            if story_mention is not None and settings.story_mentions_enabled:
                mentions.append(story_mention)

        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            field = change.get("field")
            if field not in MENTION_FIELDS:
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                continue

            from_user = value.get("from") or {}
            media = value.get("media") or {}
            media_id = (
                str(media.get("id"))
                if isinstance(media, dict) and media.get("id")
                else value.get("media_id")
            )

            comment_id = value.get("comment_id")
            if not comment_id and field == "mentions":
                raw_id = value.get("id")
                if raw_id and str(raw_id) != str(media_id):
                    comment_id = raw_id

            mention_id = comment_id or value.get("id") or media_id
            if not mention_id:
                log_event(
                    logger,
                    logging.INFO,
                    "mention_missing_id",
                    field=field,
                    value=value,
                )
                continue

            mentions.append(
                MentionCreate(
                    mention_id=str(mention_id),
                    mention_type=str(field),
                    username=from_user.get("username", "unknown") if isinstance(from_user, dict) else "unknown",
                    text=value.get("text", "") or value.get("message", "") or "",
                    comment_id=str(comment_id) if comment_id else None,
                    from_id=str(from_user.get("id", "")) if isinstance(from_user, dict) and from_user.get("id") else None,
                    media_id=str(media_id) if media_id else None,
                    account_id=account_id,
                )
            )

    return mentions


@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    comment_processor: CommentProcessor = Depends(get_comment_processor),
    message_processor: MessageProcessor = Depends(get_message_processor),
    mention_processor: MentionProcessor = Depends(get_mention_processor),
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

    comments = _extract_comments(body) if settings.comments_enabled else []
    messages = _extract_messages(body, settings) if settings.messages_enabled else []
    mentions = _extract_mentions(body, settings) if (
        settings.mentions_enabled or settings.story_mentions_enabled
    ) else []

    log_event(
        logger,
        logging.INFO,
        "webhook_dispatch",
        client_ip=client_ip,
        comment_count=len(comments),
        message_count=len(messages),
        mention_count=len(mentions),
    )

    for comment_data in comments:
        background_tasks.add_task(comment_processor.process, comment_data)

    for message_data in messages:
        background_tasks.add_task(message_processor.process, message_data)

    for mention_data in mentions:
        background_tasks.add_task(mention_processor.process, mention_data)

    return {
        "status": "ok",
        "comments_queued": str(len(comments)),
        "messages_queued": str(len(messages)),
        "mentions_queued": str(len(mentions)),
    }
