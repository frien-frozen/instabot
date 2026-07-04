"""Route normalized webhook events to platform-specific handlers."""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.instagram import InstagramAdapter
from app.config import Settings
from app.schemas.events import BaseEvent, CommentEvent, MentionEvent, MessageEvent
from app.services.comment_service import CommentService
from app.services.mention_service import MentionService
from app.services.message_service import MessageService
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


class EventDispatcher:
    """
    Central webhook router.

    Webhook → Detect Event → Comment | Message | Mention → Handler
    """

    def __init__(
        self,
        settings: Settings,
        comment_service: CommentService,
        message_service: MessageService,
        mention_service: MentionService,
        instagram_adapter: InstagramAdapter | None = None,
    ) -> None:
        self._settings = settings
        self._comments = comment_service
        self._messages = message_service
        self._mentions = mention_service
        self._instagram = instagram_adapter or InstagramAdapter()

    def parse_events(self, body: dict[str, Any]) -> list[BaseEvent]:
        return self._instagram.parse_webhook(body, self._settings)

    async def dispatch(self, event: BaseEvent) -> None:
        log_event(
            logger,
            logging.INFO,
            "event_dispatch",
            platform=event.platform,
            event_type=event.event_type,
            external_event_id=event.external_event_id,
            account_external_id=event.account_external_id,
        )

        if isinstance(event, CommentEvent):
            await self._comments.handle(event)
        elif isinstance(event, MessageEvent):
            await self._messages.handle(event)
        elif isinstance(event, MentionEvent):
            await self._mentions.handle(event)
        else:
            log_event(
                logger,
                logging.WARNING,
                "unknown_event_type",
                event_type=getattr(event, "event_type", "unknown"),
            )

    async def dispatch_webhook(self, body: dict[str, Any]) -> dict[str, int]:
        events = self.parse_events(body)
        counts = {"comment": 0, "message": 0, "mention": 0, "total": len(events)}

        for event in events:
            if isinstance(event, CommentEvent):
                counts["comment"] += 1
            elif isinstance(event, MessageEvent):
                counts["message"] += 1
            elif isinstance(event, MentionEvent):
                counts["mention"] += 1

        log_event(
            logger,
            logging.INFO,
            "webhook_dispatch",
            comment_count=counts["comment"],
            message_count=counts["message"],
            mention_count=counts["mention"],
            total_events=counts["total"],
        )
        return counts

    async def dispatch_events(self, events: list[BaseEvent]) -> None:
        for event in events:
            await self.dispatch(event)
