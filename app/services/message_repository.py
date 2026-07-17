"""Message and conversation persistence."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from app.database import MongoSession, next_id
from app.models.conversation import Conversation
from app.models.message import Message
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


class MessageRepository:
    """Data access layer for Instagram DM conversations and messages."""

    def __init__(self, session: MongoSession) -> None:
        self._session = session

    async def has_processed_message(self, message_id: str) -> bool:
        """Return True if this incoming message already received a reply."""
        message = await Message.find_one(Message.message_id == message_id)
        if message is None:
            return False
        if message.direction == "outgoing":
            return True
        return message.reply_status in ("sent", "skipped")

    async def get_incoming_message(self, message_id: str) -> Message | None:
        return await Message.find_one(
            Message.message_id == message_id,
            Message.direction == "incoming",
        )

    async def get_or_create_conversation(
        self,
        user_id: str,
        *,
        account_id: str | None = None,
        username: str | None = None,
    ) -> Conversation:
        """Find or create a conversation thread for an Instagram user."""
        if account_id:
            conversation = await Conversation.find_one(
                Conversation.user_id == user_id,
                Conversation.account_id == account_id,
            )
        else:
            conversation = await Conversation.find_one(Conversation.user_id == user_id)

        if conversation is None:
            conversation = Conversation(
                id=await next_id("conversations"),
                user_id=user_id,
                account_id=account_id,
                username=username,
            )
            await conversation.insert()
            log_event(
                logger,
                logging.INFO,
                "conversation_created",
                user_id=user_id,
                account_id=account_id,
            )
        elif username and not conversation.username:
            conversation.username = username
            await conversation.save()

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
        existing = await Message.find_one(Message.message_id == message_id)
        if existing is not None:
            log_event(
                logger,
                logging.INFO,
                "message_duplicate_skipped",
                message_id=message_id,
            )
            return None

        message = Message(
            id=await next_id("messages"),
            message_id=message_id,
            conversation_id=conversation.id,  # type: ignore[arg-type]
            sender_id=sender_id,
            text=text,
            direction=direction,
            timestamp=timestamp,
            reply_status="pending" if direction == "incoming" else None,
        )
        try:
            await message.insert()
        except DuplicateKeyError:
            log_event(
                logger,
                logging.INFO,
                "message_duplicate_skipped",
                message_id=message_id,
            )
            return None

        conversation.last_message = text
        conversation.updated_at = datetime.now(timezone.utc)
        await conversation.save()

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
        query = Message.find(Message.conversation_id == conversation_id)
        if exclude_message_id:
            query = Message.find(
                Message.conversation_id == conversation_id,
                Message.message_id != exclude_message_id,
            )

        messages = await query.sort([("timestamp", 1), ("_id", 1)]).to_list()
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
        if account_id:
            return await Conversation.find_one(
                Conversation.user_id == user_id,
                Conversation.account_id == account_id,
            )
        return await Conversation.find_one(Conversation.user_id == user_id)

    async def mark_reply_sent(self, message_id: str) -> None:
        message = await self.get_incoming_message(message_id)
        if message is None:
            return
        message.reply_status = "sent"
        message.reply_error = None
        await message.save()

    async def mark_reply_failed(self, message_id: str, error: str) -> None:
        message = await self.get_incoming_message(message_id)
        if message is None:
            return
        message.reply_status = "failed"
        message.reply_error = error[:4000]
        await message.save()

    async def mark_reply_skipped(self, message_id: str) -> None:
        message = await self.get_incoming_message(message_id)
        if message is None:
            return
        message.reply_status = "skipped"
        message.reply_error = None
        await message.save()

    @staticmethod
    def timestamp_from_ms(ms: int | None) -> datetime | None:
        """Convert Instagram millisecond timestamp to datetime."""
        if ms is None:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
