"""Instagram platform adapter."""

from __future__ import annotations

from typing import Any

from app.adapters.base import PlatformAdapter
from app.config import Settings
from app.schemas.events import BaseEvent, CommentEvent, MentionEvent, MessageEvent
from app.utils.logging import get_logger, log_event
import logging

logger = get_logger(__name__)

COMMENT_FIELDS = frozenset({"comments", "live_comments"})
MESSAGE_FIELDS = frozenset({"messages", "messaging"})
MENTION_FIELDS = frozenset({"mentions", "story_mentions"})


def _parse_message_timestamp(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.isdigit():
        raw = int(raw)
    if isinstance(raw, (int, float)):
        value = int(raw)
        if value < 10_000_000_000:
            return value * 1000
        return value
    return None


def _resolve_account_id(entry_id: str, settings: Settings) -> str:
    if entry_id and entry_id not in ("0", "unknown"):
        return entry_id
    return settings.resolved_instagram_user_id or entry_id or "unknown"


class InstagramAdapter(PlatformAdapter):
    platform = "instagram"

    def normalize_body(self, body: dict[str, Any], settings: Settings) -> dict[str, Any]:
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
            return {"object": "instagram", "entry": [{"id": account_id, "changes": [body]}]}
        return body

    def parse_webhook(self, body: dict[str, Any], settings: Settings) -> list[BaseEvent]:
        body = self.normalize_body(body, settings)
        if body.get("object") != "instagram":
            return []

        events: list[BaseEvent] = []
        for entry in body.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            account_id = _resolve_account_id(str(entry.get("id", "")), settings)
            events.extend(self._parse_messaging(entry, account_id))
            events.extend(self._parse_changes(entry, account_id))
        return events

    def _parse_messaging(self, entry: dict[str, Any], account_id: str) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        for event in entry.get("messaging") or []:
            if not isinstance(event, dict):
                continue
            if event.get("read") or event.get("delivery") or event.get("reaction"):
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            if message.get("is_echo") or message.get("attachments"):
                continue
            text = message.get("text")
            mid = message.get("mid")
            sender = event.get("sender") or {}
            recipient = event.get("recipient") or {}
            if not mid or not text or not isinstance(sender, dict):
                continue
            events.append(
                MessageEvent(
                    platform=self.platform,
                    event_type="message",
                    account_external_id=account_id,
                    external_event_id=str(mid),
                    message_id=str(mid),
                    sender_id=str(sender.get("id", "")),
                    recipient_id=str(recipient.get("id", "")) if isinstance(recipient, dict) else "",
                    text=str(text),
                    timestamp=_parse_message_timestamp(event.get("timestamp")),
                    is_echo=bool(message.get("is_echo")),
                    raw_payload=event,
                )
            )
        return events

    def _parse_changes(self, entry: dict[str, Any], account_id: str) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            field = change.get("field")
            value = change.get("value")
            if not isinstance(value, dict):
                continue

            if field in COMMENT_FIELDS:
                comment_id = value.get("id")
                if not comment_id:
                    continue
                from_user = value.get("from") or {}
                media = value.get("media") or {}
                events.append(
                    CommentEvent(
                        platform=self.platform,
                        event_type="comment",
                        account_external_id=account_id,
                        external_event_id=str(comment_id),
                        comment_id=str(comment_id),
                        username=from_user.get("username", "unknown") if isinstance(from_user, dict) else "unknown",
                        text=value.get("text", "") or "",
                        media_id=str(media.get("id", "")) if isinstance(media, dict) else "",
                        from_id=str(from_user.get("id", "")) if isinstance(from_user, dict) and from_user.get("id") else None,
                        parent_comment_id=value.get("parent_id"),
                        raw_payload=change,
                    )
                )
            elif field in MESSAGE_FIELDS:
                message = value.get("message") or {}
                if not isinstance(message, dict) or message.get("is_echo"):
                    continue
                text = message.get("text")
                mid = message.get("mid")
                sender = value.get("sender") or {}
                if not mid or not text or not isinstance(sender, dict):
                    continue
                recipient = value.get("recipient") or {}
                events.append(
                    MessageEvent(
                        platform=self.platform,
                        event_type="message",
                        account_external_id=account_id,
                        external_event_id=str(mid),
                        message_id=str(mid),
                        sender_id=str(sender.get("id", "")),
                        recipient_id=str(recipient.get("id", "")) if isinstance(recipient, dict) else "",
                        text=str(text),
                        timestamp=_parse_message_timestamp(value.get("timestamp")),
                        is_echo=bool(message.get("is_echo")),
                        raw_payload=change,
                    )
                )
            elif field in MENTION_FIELDS:
                mention_id = value.get("id") or value.get("comment_id") or value.get("media_id")
                if not mention_id:
                    log_event(
                        logger,
                        logging.INFO,
                        "mention_missing_id",
                        field=field,
                        value=value,
                    )
                    continue
                from_user = value.get("from") or {}
                media = value.get("media") or {}
                events.append(
                    MentionEvent(
                        platform=self.platform,
                        event_type="mention",
                        account_external_id=account_id,
                        external_event_id=str(mention_id),
                        mention_id=str(mention_id),
                        username=from_user.get("username", "unknown") if isinstance(from_user, dict) else "unknown",
                        text=value.get("text", "") or value.get("message", "") or "",
                        mention_type=str(field),
                        media_id=str(media.get("id", "")) if isinstance(media, dict) else value.get("media_id"),
                        comment_id=str(value.get("comment_id", "")) if value.get("comment_id") else None,
                        from_id=str(from_user.get("id", "")) if isinstance(from_user, dict) and from_user.get("id") else None,
                        raw_payload=change,
                    )
                )
        return events
