"""Detailed webhook event logging — runs before any filtering."""

from __future__ import annotations

import logging
from typing import Any

from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


def log_all_webhook_events(body: dict[str, Any], *, client_ip: str) -> None:
    """
    Log every webhook event with full detail before any filtering or processing.

    Does not skip, ignore, or mutate any events.
    """
    object_type = body.get("object")
    entries = body.get("entry") or []

    log_event(
        logger,
        logging.INFO,
        "webhook_events_begin",
        client_ip=client_ip,
        object_type=object_type,
        entry_count=len(entries),
    )

    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue

        entry_id = entry.get("id")
        entry_time = entry.get("time")

        log_event(
            logger,
            logging.INFO,
            "webhook_entry",
            client_ip=client_ip,
            entry_index=entry_index,
            entry_id=entry_id,
            entry_time=entry_time,
        )

        _log_messaging_events(entry, client_ip=client_ip, entry_index=entry_index, entry_id=entry_id)
        _log_change_events(entry, client_ip=client_ip, entry_index=entry_index, entry_id=entry_id)

    log_event(
        logger,
        logging.INFO,
        "webhook_events_end",
        client_ip=client_ip,
        object_type=object_type,
        entry_count=len(entries),
    )


def _log_messaging_events(
    entry: dict[str, Any],
    *,
    client_ip: str,
    entry_index: int,
    entry_id: Any,
) -> None:
    """Log all messaging array events (DMs, echoes, read, delivery, reactions)."""
    messaging = entry.get("messaging") or []
    for msg_index, event in enumerate(messaging):
        if not isinstance(event, dict):
            continue

        sender = event.get("sender") or {}
        recipient = event.get("recipient") or {}
        message = event.get("message") or {}

        event_types: list[str] = ["messaging"]
        if event.get("read"):
            event_types.append("read")
        if event.get("delivery"):
            event_types.append("delivery")
        if event.get("reaction"):
            event_types.append("reaction")
        if message.get("is_echo"):
            event_types.append("echo")
        if message.get("attachments"):
            event_types.append("attachment")

        log_event(
            logger,
            logging.INFO,
            "webhook_messaging_event",
            client_ip=client_ip,
            entry_index=entry_index,
            entry_id=entry_id,
            msg_index=msg_index,
            event_types=event_types,
            sender_id=sender.get("id") if isinstance(sender, dict) else sender,
            recipient_id=recipient.get("id") if isinstance(recipient, dict) else recipient,
            message_id=message.get("mid") if isinstance(message, dict) else None,
            text=message.get("text") if isinstance(message, dict) else None,
            is_echo=message.get("is_echo") if isinstance(message, dict) else None,
            timestamp=event.get("timestamp"),
            has_read=bool(event.get("read")),
            has_delivery=bool(event.get("delivery")),
            has_reaction=bool(event.get("reaction")),
            has_attachments=bool(message.get("attachments")) if isinstance(message, dict) else False,
            raw_event=event,
        )


def _log_change_events(
    entry: dict[str, Any],
    *,
    client_ip: str,
    entry_index: int,
    entry_id: Any,
) -> None:
    """Log all change events (comments, live_comments, etc.)."""
    changes = entry.get("changes") or []
    for change_index, change in enumerate(changes):
        if not isinstance(change, dict):
            continue

        field = change.get("field")
        value = change.get("value") or {}

        if not isinstance(value, dict):
            log_event(
                logger,
                logging.INFO,
                "webhook_change_event",
                client_ip=client_ip,
                entry_index=entry_index,
                entry_id=entry_id,
                change_index=change_index,
                field=field,
                value_type=type(value).__name__,
                raw_change=change,
            )
            continue

        from_user = value.get("from") or {}
        media = value.get("media") or {}
        comment_id = value.get("id")
        parent_id = value.get("parent_id")
        media_id = media.get("id") if isinstance(media, dict) else None
        from_id = from_user.get("id") if isinstance(from_user, dict) else None
        username = from_user.get("username") if isinstance(from_user, dict) else None
        text = value.get("text")

        is_reply = bool(
            parent_id is not None
            and media_id is not None
            and str(parent_id) != str(media_id)
        )

        log_event(
            logger,
            logging.INFO,
            "webhook_comment_event",
            client_ip=client_ip,
            entry_index=entry_index,
            entry_id=entry_id,
            change_index=change_index,
            event_type=field,
            field=field,
            media_id=media_id,
            comment_id=comment_id,
            parent_id=parent_id,
            from_id=from_id,
            from_username=username,
            text=text,
            is_reply=is_reply,
            raw_change=change,
        )
