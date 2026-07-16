"""Parse Instagram webhook payloads into queued events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.models.event import EventType


@dataclass(frozen=True)
class ParsedEvent:
    event_type: str
    event_id: str
    sender_id: str | None
    recipient_id: str | None
    payload: dict[str, Any]


COMMENT_FIELDS = frozenset({"comments", "live_comments"})
MESSAGE_FIELDS = frozenset({"messages", "messaging"})
MENTION_FIELDS = frozenset({"mentions", "story_mentions"})


class WebhookParser:
    """Extract normalized events from Meta webhook bodies."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(self, body: dict[str, Any]) -> list[ParsedEvent]:
        body = self.normalize_body(body)
        if body.get("object") != "instagram":
            return []

        events: list[ParsedEvent] = []
        if self._settings.comments_enabled:
            events.extend(self._extract_comments(body))
        if self._settings.messages_enabled:
            events.extend(self._extract_messages(body))
        if self._settings.mentions_enabled:
            events.extend(self._extract_mentions(body))
        return events

    def normalize_body(self, body: dict[str, Any]) -> dict[str, Any]:
        if "object" in body and "entry" in body:
            return body
        if "field" in body and "value" in body:
            account_id = self._settings.resolved_instagram_user_id or "unknown"
            return {
                "object": "instagram",
                "entry": [{"id": account_id, "changes": [body]}],
            }
        return body

    def _resolve_account_id(self, entry_id: str) -> str:
        if entry_id and entry_id not in ("0", "unknown"):
            return entry_id
        return self._settings.resolved_instagram_user_id or entry_id or "unknown"

    @staticmethod
    def _parse_timestamp(raw: Any) -> int | None:
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

    def _extract_comments(self, body: dict[str, Any]) -> list[ParsedEvent]:
        events: list[ParsedEvent] = []
        for entry in body.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            account_id = self._resolve_account_id(str(entry.get("id", "")))
            for change in entry.get("changes") or []:
                if not isinstance(change, dict) or change.get("field") not in COMMENT_FIELDS:
                    continue
                value = change.get("value")
                if not isinstance(value, dict) or not value.get("id"):
                    continue
                from_user = value.get("from") or {}
                media = value.get("media") or {}
                comment_id = str(value["id"])
                events.append(
                    ParsedEvent(
                        event_type=EventType.COMMENT,
                        event_id=f"comment:{comment_id}",
                        sender_id=str(from_user.get("id")) if isinstance(from_user, dict) and from_user.get("id") else None,
                        recipient_id=account_id,
                        payload={
                            "comment_id": comment_id,
                            "username": from_user.get("username", "unknown") if isinstance(from_user, dict) else "unknown",
                            "message": value.get("text", "") or "",
                            "media_id": str(media.get("id", "")) if isinstance(media, dict) else "",
                            "from_id": str(from_user.get("id")) if isinstance(from_user, dict) and from_user.get("id") else None,
                            "parent_comment_id": value.get("parent_id"),
                            "account_id": account_id,
                        },
                    )
                )
        return events

    def _extract_messages(self, body: dict[str, Any]) -> list[ParsedEvent]:
        events: list[ParsedEvent] = []
        for entry in body.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            account_id = self._resolve_account_id(str(entry.get("id", "")))

            for event in entry.get("messaging") or []:
                if not isinstance(event, dict):
                    continue
                if self._story_mention_event(event) is not None:
                    continue
                if event.get("read") or event.get("delivery") or event.get("reaction"):
                    continue
                parsed = self._message_event(account_id, event.get("sender"), event.get("recipient"), event.get("message"), event.get("timestamp"))
                if parsed:
                    events.append(parsed)

            for change in entry.get("changes") or []:
                if not isinstance(change, dict) or change.get("field") not in MESSAGE_FIELDS:
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                parsed = self._message_event(
                    account_id,
                    value.get("sender"),
                    value.get("recipient"),
                    value.get("message"),
                    value.get("timestamp"),
                )
                if parsed:
                    events.append(parsed)
        return events

    def _message_event(self, account_id: str, sender: Any, recipient: Any, message: Any, timestamp: Any) -> ParsedEvent | None:
        if not isinstance(message, dict):
            return None
        message_id = message.get("mid")
        if not message_id or not isinstance(sender, dict):
            return None
        if message.get("is_echo"):
            return None
        text = message.get("text")
        if not text or not str(text).strip():
            return None
        sender_id = str(sender.get("id", ""))
        return ParsedEvent(
            event_type=EventType.DM,
            event_id=f"dm:{message_id}",
            sender_id=sender_id,
            recipient_id=str(recipient.get("id", "")) if isinstance(recipient, dict) else account_id,
            payload={
                "message_id": str(message_id),
                "sender_id": sender_id,
                "recipient_id": str(recipient.get("id", "")) if isinstance(recipient, dict) else "",
                "text": str(text),
                "timestamp": self._parse_timestamp(timestamp),
                "account_id": account_id,
                "is_echo": False,
            },
        )

    def _story_mention_event(self, event: dict[str, Any]) -> ParsedEvent | None:
        referral = event.get("referral")
        if not isinstance(referral, dict) or str(referral.get("source", "")).upper() != "STORY_MENTION":
            return None
        sender = event.get("sender") or {}
        if not isinstance(sender, dict) or not sender.get("id"):
            return None
        message = event.get("message") or {}
        story = referral.get("story") or {}
        mention_id = message.get("mid") or story.get("id") or f"story_{sender.get('id')}_{event.get('timestamp', '')}"
        return ParsedEvent(
            event_type=EventType.STORY_MENTION,
            event_id=f"story_mention:{mention_id}",
            sender_id=str(sender.get("id")),
            recipient_id=None,
            payload={
                "mention_id": str(mention_id),
                "mention_type": "story_mentions",
                "username": str(sender.get("username", "unknown")),
                "text": str(message.get("text", "") or "Story mention"),
                "from_id": str(sender.get("id")),
                "media_id": str(story.get("id")) if isinstance(story, dict) and story.get("id") else None,
            },
        )

    def _extract_mentions(self, body: dict[str, Any]) -> list[ParsedEvent]:
        events: list[ParsedEvent] = []
        for entry in body.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            account_id = self._resolve_account_id(str(entry.get("id", "")))

            for event in entry.get("messaging") or []:
                if not isinstance(event, dict):
                    continue
                story = self._story_mention_event(event)
                if story and self._settings.story_mentions_enabled:
                    events.append(story)

            for change in entry.get("changes") or []:
                if not isinstance(change, dict) or change.get("field") not in MENTION_FIELDS:
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                from_user = value.get("from") or {}
                media = value.get("media") or {}
                media_id = str(media.get("id")) if isinstance(media, dict) and media.get("id") else value.get("media_id")
                field = change.get("field")
                comment_id = value.get("comment_id") or (value.get("id") if field == "mentions" else None)
                mention_id = comment_id or value.get("id") or media_id
                if not mention_id:
                    continue
                events.append(
                    ParsedEvent(
                        event_type=EventType.MENTION,
                        event_id=f"mention:{mention_id}",
                        sender_id=str(from_user.get("id")) if isinstance(from_user, dict) and from_user.get("id") else None,
                        recipient_id=account_id,
                        payload={
                            "mention_id": str(mention_id),
                            "mention_type": str(field),
                            "username": from_user.get("username", "unknown") if isinstance(from_user, dict) else "unknown",
                            "text": value.get("text", "") or value.get("message", "") or "",
                            "comment_id": str(comment_id) if comment_id else None,
                            "from_id": str(from_user.get("id")) if isinstance(from_user, dict) and from_user.get("id") else None,
                            "media_id": str(media_id) if media_id else None,
                            "account_id": account_id,
                        },
                    )
                )
        return events
