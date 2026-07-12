"""Event queue persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event, EventStatus

RETRY_DELAYS_SECONDS = (2, 5, 10, 20, 40)
MAX_ATTEMPTS = len(RETRY_DELAYS_SECONDS) + 1


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, parsed: "ParsedEvent") -> Event | None:
        """Insert event if event_id is new; return None on duplicate."""
        from app.instagram.parser import ParsedEvent  # noqa: F401

        existing = await self._session.execute(
            select(Event.id).where(Event.event_id == parsed.event_id)
        )
        if existing.scalar_one_or_none() is not None:
            return None

        row = Event(
            event_type=parsed.event_type,
            event_id=parsed.event_id,
            sender_id=parsed.sender_id,
            recipient_id=parsed.recipient_id,
            payload=parsed.payload,
            status=EventStatus.PENDING,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def claim_batch(self, *, limit: int = 20) -> list[Event]:
        now = datetime.now(timezone.utc)
        result = await self._session.execute(
            select(Event)
            .where(
                Event.status == EventStatus.PENDING,
                (Event.next_retry_at.is_(None)) | (Event.next_retry_at <= now),
            )
            .order_by(Event.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = EventStatus.PROCESSING
        await self._session.flush()
        return rows

    async def mark_completed(self, event: Event, *, task_id: int | None = None) -> None:
        event.status = EventStatus.COMPLETED
        event.processed_at = datetime.now(timezone.utc)
        event.task_id = task_id
        event.last_error = None
        await self._session.flush()

    async def mark_failed(self, event: Event, error: str) -> None:
        event.attempts += 1
        event.last_error = error[:4000]
        if event.attempts >= MAX_ATTEMPTS:
            event.status = EventStatus.FAILED
            event.processed_at = datetime.now(timezone.utc)
        else:
            delay = RETRY_DELAYS_SECONDS[min(event.attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)]
            event.status = EventStatus.PENDING
            event.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        await self._session.flush()

    async def release_stuck(self, *, older_than_minutes: int = 10) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        result = await self._session.execute(
            update(Event)
            .where(Event.status == EventStatus.PROCESSING, Event.created_at < cutoff)
            .values(status=EventStatus.PENDING)
        )
        return result.rowcount or 0

    async def count_by_status(self) -> dict[str, int]:
        from sqlalchemy import func

        result = await self._session.execute(
            select(Event.status, func.count()).group_by(Event.status)
        )
        return {status: count for status, count in result.all()}

    async def get(self, event_db_id: int) -> Event | None:
        return await self._session.get(Event, event_db_id)
