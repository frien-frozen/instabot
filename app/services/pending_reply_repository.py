"""Persist and replay pending replies after crashes or API failures."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pending_reply import PendingReply
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


class PendingReplyRepository:
    """Queue failed or incomplete events for retry on the next app start."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        event_type: str,
        external_event_id: str,
        payload: dict[str, Any],
    ) -> PendingReply:
        result = await self._session.execute(
            select(PendingReply).where(
                PendingReply.event_type == event_type,
                PendingReply.external_event_id == external_event_id,
            )
        )
        row = result.scalar_one_or_none()
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)

        if row is None:
            row = PendingReply(
                event_type=event_type,
                external_event_id=external_event_id,
                payload=encoded,
            )
            self._session.add(row)
        else:
            row.payload = encoded
            row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()
        return row

    async def complete(self, event_type: str, external_event_id: str) -> None:
        await self._session.execute(
            delete(PendingReply).where(
                PendingReply.event_type == event_type,
                PendingReply.external_event_id == external_event_id,
            )
        )

    async def record_failure(
        self,
        event_type: str,
        external_event_id: str,
        error: str,
    ) -> None:
        result = await self._session.execute(
            select(PendingReply).where(
                PendingReply.event_type == event_type,
                PendingReply.external_event_id == external_event_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return

        row.attempts += 1
        row.last_error = error[:4000]
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

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
        result = await self._session.execute(
            select(PendingReply)
            .order_by(PendingReply.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    def decode_payload(row: PendingReply) -> dict[str, Any]:
        return json.loads(row.payload)
