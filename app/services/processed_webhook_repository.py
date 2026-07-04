"""Persist processed webhook events for duplicate protection."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.processed_webhook import ProcessedWebhook


class ProcessedWebhookRepository:
    """Check and record processed webhook event IDs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_processed(self, event_type: str, external_event_id: str) -> bool:
        result = await self._session.execute(
            select(ProcessedWebhook.id).where(
                ProcessedWebhook.event_type == event_type,
                ProcessedWebhook.external_event_id == external_event_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def mark_processed(self, event_type: str, external_event_id: str) -> None:
        if await self.is_processed(event_type, external_event_id):
            return
        self._session.add(
            ProcessedWebhook(
                event_type=event_type,
                external_event_id=external_event_id,
            )
        )
