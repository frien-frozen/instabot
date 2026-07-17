"""Persist processed webhook events for duplicate protection."""

from __future__ import annotations

from pymongo.errors import DuplicateKeyError

from app.database import MongoSession, next_id
from app.models.processed_webhook import ProcessedWebhook


class ProcessedWebhookRepository:
    """Check and record processed webhook event IDs."""

    def __init__(self, session: MongoSession) -> None:
        self._session = session

    async def is_processed(self, event_type: str, external_event_id: str) -> bool:
        row = await ProcessedWebhook.find_one(
            ProcessedWebhook.event_type == event_type,
            ProcessedWebhook.external_event_id == external_event_id,
        )
        return row is not None

    async def mark_processed(self, event_type: str, external_event_id: str) -> None:
        if await self.is_processed(event_type, external_event_id):
            return
        try:
            await ProcessedWebhook(
                id=await next_id("processed_webhooks"),
                event_type=event_type,
                external_event_id=external_event_id,
            ).insert()
        except DuplicateKeyError:
            return
