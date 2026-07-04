"""Conversation memory, audit logs, and processed-event deduplication."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_log import ConversationLog
from app.models.message import Message
from app.models.processed_event import ProcessedEvent
from app.utils.logging import get_logger

logger = get_logger(__name__)

MAX_HISTORY_MESSAGES = 20
MAX_HISTORY_CHARS = 4000


class ConversationService:
    """Store logs, dedupe events, and build conversation history."""

    async def is_processed(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        platform: str,
        event_type: str,
        external_event_id: str,
    ) -> bool:
        result = await session.execute(
            select(ProcessedEvent.id).where(
                ProcessedEvent.account_id == account_id,
                ProcessedEvent.platform == platform,
                ProcessedEvent.event_type == event_type,
                ProcessedEvent.external_event_id == external_event_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def mark_processed(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        platform: str,
        event_type: str,
        external_event_id: str,
    ) -> None:
        session.add(
            ProcessedEvent(
                account_id=account_id,
                platform=platform,
                event_type=event_type,
                external_event_id=external_event_id,
            )
        )

    async def log_event(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        platform: str,
        event_type: str,
        external_event_id: str | None = None,
        external_user_id: str | None = None,
        username: str | None = None,
        incoming_text: str | None = None,
        generated_reply: str | None = None,
        api_status: str | None = None,
        api_response: Any = None,
        duration_ms: int | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationLog:
        response_text: str | None = None
        if api_response is not None:
            response_text = (
                json.dumps(api_response) if isinstance(api_response, dict) else str(api_response)
            )
        metadata_text = json.dumps(metadata) if metadata else None

        entry = ConversationLog(
            account_id=account_id,
            platform=platform,
            event_type=event_type,
            external_event_id=external_event_id,
            external_user_id=external_user_id,
            username=username,
            incoming_text=incoming_text,
            generated_reply=generated_reply,
            api_status=api_status,
            api_response=response_text,
            duration_ms=duration_ms,
            error=error,
            metadata_json=metadata_text,
        )
        session.add(entry)
        await session.flush()
        return entry

    async def build_history(
        self,
        session: AsyncSession,
        *,
        account_id: int,
        account_external_id: str,
        user_id: str,
    ) -> str | None:
        """Return recent DM history for AI context."""
        conv_result = await session.execute(
            select(Conversation).where(
                Conversation.account_id == account_external_id,
                Conversation.user_id == user_id,
            )
        )
        conversation = conv_result.scalar_one_or_none()
        if conversation is None:
            return None

        msg_result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.id.desc())
            .limit(MAX_HISTORY_MESSAGES)
        )
        messages = list(reversed(msg_result.scalars().all()))
        if not messages:
            return None

        lines: list[str] = []
        total = 0
        for msg in messages:
            role = "User" if msg.direction == "incoming" else "You"
            line = f"{role}: {msg.text}"
            if total + len(line) > MAX_HISTORY_CHARS:
                break
            lines.append(line)
            total += len(line)

        return "Recent conversation:\n" + "\n".join(lines)

    @staticmethod
    def timestamp_from_ms(ms: int | None) -> datetime | None:
        if ms is None:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
