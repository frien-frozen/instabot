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
        """Return True if this incoming message already received a reply."""
        result = await self._session.execute(
            select(Message).where(Message.message_id == message_id)
        )
        message = result.scalar_one_or_none()
        if message is None:
            return False
        if message.direction == "outgoing":
            return True
        return message.reply_status in ("sent", "skipped")

    async def get_incoming_message(self, message_id: str) -> Message | None:
        result = await self._session.execute(
            select(Message).where(
                Message.message_id == message_id,
                Message.direction == "incoming",
            )
        )
        return result.scalar_one_or_none()

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
            reply_status="pending" if direction == "incoming" else None,
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

    async def build_conversation_history(
        self,
        conversation_id: int,
        *,
        bot_user_id: str,
        limit: int = 20,
        exclude_message_id: str | None = None,
    ) -> str:
        """Format recent DM turns for Gemini context."""
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.timestamp.asc().nulls_last(), Message.id.asc())
        )
        if exclude_message_id:
            query = query.where(Message.message_id != exclude_message_id)

        result = await self._session.execute(query)
        messages = list(result.scalars().all())
        if limit > 0:
            messages = messages[-limit:]

        lines: list[str] = []
        for message in messages:
            if not message.text or not message.text.strip():
                continue
            role = "You" if message.direction == "outgoing" or message.sender_id == bot_user_id else "User"
            lines.append(f"{role}: {message.text.strip()}")

        return "\n".join(lines)

    async def get_conversation_by_user(
        self,
        user_id: str,
        *,
        account_id: str | None = None,
    ) -> Conversation | None:
        query = select(Conversation).where(Conversation.user_id == user_id)
        if account_id:
            query = query.where(Conversation.account_id == account_id)
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def mark_reply_sent(self, message_id: str) -> None:
        message = await self.get_incoming_message(message_id)
        if message is None:
            return
        message.reply_status = "sent"
        message.reply_error = None
        await self._session.flush()

    async def mark_reply_failed(self, message_id: str, error: str) -> None:
        message = await self.get_incoming_message(message_id)
        if message is None:
            return
        message.reply_status = "failed"
        message.reply_error = error[:4000]
        await self._session.flush()

    async def mark_reply_skipped(self, message_id: str) -> None:
        message = await self.get_incoming_message(message_id)
        if message is None:
            return
        message.reply_status = "skipped"
        message.reply_error = None
        await self._session.flush()

    @staticmethod
    def timestamp_from_ms(ms: int | None) -> datetime | None:
        """Convert Instagram millisecond timestamp to datetime."""
        if ms is None:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
