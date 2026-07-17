"""Event queue persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.database import MongoSession, next_id
from app.models.event import Event, EventStatus

RETRY_DELAYS_SECONDS = (2, 5, 10, 20, 40)
MAX_ATTEMPTS = len(RETRY_DELAYS_SECONDS) + 1


class EventRepository:
    def __init__(self, session: MongoSession) -> None:
        self._session = session

    async def enqueue(self, parsed: "ParsedEvent") -> Event | None:
        """Insert event if event_id is new; return None on duplicate."""
        from app.instagram.parser import ParsedEvent  # noqa: F401

        existing = await Event.find_one(Event.event_id == parsed.event_id)
        if existing is not None:
            return None

        row = Event(
            id=await next_id("events"),
            event_type=parsed.event_type,
            event_id=parsed.event_id,
            sender_id=parsed.sender_id,
            recipient_id=parsed.recipient_id,
            payload=parsed.payload,
            status=EventStatus.PENDING,
        )
        try:
            await row.insert()
        except DuplicateKeyError:
            return None
        return row

    async def claim_batch(self, *, limit: int = 20) -> list[Event]:
        now = datetime.now(timezone.utc)
        collection = Event.get_motor_collection()
        rows: list[Event] = []
        for _ in range(limit):
            doc = await collection.find_one_and_update(
                {
                    "status": EventStatus.PENDING,
                    "$or": [
                        {"next_retry_at": None},
                        {"next_retry_at": {"$lte": now}},
                    ],
                },
                {"$set": {"status": EventStatus.PROCESSING}},
                sort=[("created_at", ASCENDING)],
                return_document=ReturnDocument.AFTER,
            )
            if doc is None:
                break
            data = dict(doc)
            if "_id" in data:
                data["id"] = data.pop("_id")
            rows.append(Event.model_validate(data))
        return rows

    async def mark_completed(self, event: Event, *, task_id: int | None = None) -> None:
        event.status = EventStatus.COMPLETED
        event.processed_at = datetime.now(timezone.utc)
        event.task_id = task_id
        event.last_error = None
        await event.save()

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
        await event.save()

    async def release_stuck(self, *, older_than_minutes: int = 10) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        result = await Event.find(
            Event.status == EventStatus.PROCESSING,
            Event.created_at < cutoff,
        ).update({"$set": {"status": EventStatus.PENDING}})
        return int(getattr(result, "modified_count", 0) or 0)

    async def count_by_status(self) -> dict[str, int]:
        pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        counts: dict[str, int] = {}
        async for row in Event.get_motor_collection().aggregate(pipeline):
            counts[str(row["_id"])] = int(row["count"])
        return counts

    async def get(self, event_db_id: int) -> Event | None:
        return await Event.get(event_db_id)
