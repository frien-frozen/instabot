"""Message and conversation persistence."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.message import Message
from app.schemas import MessageCreate
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


class MessageRepository:
    """Data access layer for Instagram DM conversations and messages."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_processed_message(self, message_id: str) -> bool:
        """Return True if this message_id was already handled."""
        result = await self._session.execute(
            select(Message).where(Message.message_id == message_id)
        )
        return result.scalar_one_or_none() is not None

    async def get_or_create_conversation(
        self,
        user_id: str,
        *,
        account_id: str | None = None,
        username: str | None = None,
    ) -> Conversation:
        """Find or create a conversation thread for an Instagram user."""
        query = select(Conversation).where(Conversation.user_id == user_id)
        if account_id:
            query = query.where(Conversation.account_id == account_id)

        result = await self._session.execute(query)
        conversation = result.scalar_one_or_none()

        if conversation is None:
            conversation = Conversation(
                user_id=user_id,
                account_id=account_id,
                username=username,
            )
            self._session.add(conversation)
            await self._session.flush()
            log_event(
                logger,
                logging.INFO,
                "conversation_created",
                user_id=user_id,
                account_id=account_id,
            )
        elif username and not conversation.username:
            conversation.username = username

        return conversation

    async def store_message(
        self,
        conversation: Conversation,
        *,
        message_id: str,
        sender_id: str,
        text: str,
        direction: str,
        timestamp: datetime | None = None,
    ) -> Message | None:
        """Persist a message; return None if duplicate."""
        existing = await self._session.execute(
            select(Message).where(Message.message_id == message_id)
        )
        if existing.scalar_one_or_none() is not None:
            log_event(
                logger,
                logging.INFO,
                "message_duplicate_skipped",
                message_id=message_id,
            )
            return None

        message = Message(
            message_id=message_id,
            conversation_id=conversation.id,
            sender_id=sender_id,
            text=text,
            direction=direction,
            timestamp=timestamp,
        )
        self._session.add(message)
        conversation.last_message = text
        conversation.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

        log_event(
            logger,
            logging.INFO,
            "message_stored",
            message_id=message_id,
            direction=direction,
            conversation_id=conversation.id,
        )
        return message

    @staticmethod
    def timestamp_from_ms(ms: int | None) -> datetime | None:
        """Convert Instagram millisecond timestamp to datetime."""
        if ms is None:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
