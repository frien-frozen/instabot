"""Persist and replay pending replies after crashes or API failures."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.database import MongoSession, next_id
from app.models.pending_reply import PendingReply
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


class PendingReplyRepository:
    """Queue failed or incomplete events for retry on the next app start."""

    def __init__(self, session: MongoSession) -> None:
        self._session = session

    async def upsert(
        self,
        event_type: str,
        external_event_id: str,
        payload: dict[str, Any],
    ) -> PendingReply:
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        row = await PendingReply.find_one(
            PendingReply.event_type == event_type,
            PendingReply.external_event_id == external_event_id,
        )

        if row is None:
            row = PendingReply(
                id=await next_id("pending_replies"),
                event_type=event_type,
                external_event_id=external_event_id,
                payload=encoded,
            )
            await row.insert()
        else:
            row.payload = encoded
            row.updated_at = datetime.now(timezone.utc)
            await row.save()

        return row

    async def complete(self, event_type: str, external_event_id: str) -> None:
        await PendingReply.find(
            PendingReply.event_type == event_type,
            PendingReply.external_event_id == external_event_id,
        ).delete()

    async def record_failure(
        self,
        event_type: str,
        external_event_id: str,
        error: str,
    ) -> None:
        row = await PendingReply.find_one(
            PendingReply.event_type == event_type,
            PendingReply.external_event_id == external_event_id,
        )
        if row is None:
            return

        row.attempts += 1
        row.last_error = error[:4000]
        row.updated_at = datetime.now(timezone.utc)
        await row.save()

        log_event(
            logger,
            logging.WARNING,
            "pending_reply_failure_recorded",
            event_type=event_type,
            external_event_id=external_event_id,
            attempts=row.attempts,
            error=error,
        )

    async def list_pending(self, *, limit: int = 200) -> list[PendingReply]:
        return (
            await PendingReply.find_all()
            .sort([("created_at", 1)])
            .limit(limit)
            .to_list()
        )

    @staticmethod
    def decode_payload(row: PendingReply) -> dict[str, Any]:
        return json.loads(row.payload)
