"""Orchestrates Instagram Direct Message processing."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.schemas import MessageCreate
from app.services.gemini_service import GeminiAPIError, GeminiService
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.services.message_repository import MessageRepository
from app.utils.logging import get_logger, log_duration, log_event

logger = get_logger(__name__)


class MessageProcessor:
    """
    End-to-end Instagram DM pipeline.

    Pipeline steps:
      1. Ignore echoes and own messages
      2. Duplicate check by message_id
      3. Store incoming message
      4. Gemini reply generation
      5. Send via Instagram Messaging API
      6. Store outgoing message
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        gemini_service: GeminiService,
        instagram_service: InstagramService,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._gemini = gemini_service
        self._instagram = instagram_service

    async def process(self, data: MessageCreate) -> None:
        """Process a single incoming DM through the full pipeline."""
        with log_duration(
            logger,
            "message_processing",
            message_id=data.message_id,
            sender_id=data.sender_id,
        ):
            log_event(
                logger,
                logging.INFO,
                "incoming_dm",
                message_id=data.message_id,
                sender_id=data.sender_id,
                recipient_id=data.recipient_id,
                text=data.text,
                timestamp=data.timestamp,
                account_id=data.account_id,
                is_echo=data.is_echo,
            )

            authenticated_id = await self._instagram.get_authenticated_user_id()

            if data.is_echo:
                log_event(
                    logger,
                    logging.INFO,
                    "dm_echo_ignored",
                    message_id=data.message_id,
                    sender_id=data.sender_id,
                )
                return

            if data.sender_id == authenticated_id:
                log_event(
                    logger,
                    logging.INFO,
                    "ignoring_own_message",
                    message_id=data.message_id,
                    sender_id=data.sender_id,
                    authenticated_user_id=authenticated_id,
                )
                return

            if not data.text or not data.text.strip():
                log_event(
                    logger,
                    logging.INFO,
                    "dm_empty_text_ignored",
                    message_id=data.message_id,
                )
                return

            async with self._session_factory() as session:
                repo = MessageRepository(session)

                if await repo.has_processed_message(data.message_id):
                    log_event(
                        logger,
                        logging.INFO,
                        "duplicate_dm_skipped",
                        message_id=data.message_id,
                    )
                    return

                conversation = await repo.get_or_create_conversation(
                    user_id=data.sender_id,
                    account_id=data.account_id,
                )

                incoming = await repo.store_message(
                    conversation,
                    message_id=data.message_id,
                    sender_id=data.sender_id,
                    text=data.text,
                    direction="incoming",
                    timestamp=MessageRepository.timestamp_from_ms(data.timestamp),
                )
                if incoming is None:
                    await session.commit()
                    return

                await session.commit()

            try:
                reply_text = await self._gemini.generate_reply(data.text)
                result = await self._instagram.send_message(data.sender_id, reply_text)

                async with self._session_factory() as session:
                    repo = MessageRepository(session)
                    conversation = await repo.get_or_create_conversation(
                        user_id=data.sender_id,
                        account_id=data.account_id,
                    )
                    outgoing_id = str(
                        result.get("message_id")
                        or result.get("id")
                        or f"out_{data.message_id}"
                    )
                    await repo.store_message(
                        conversation,
                        message_id=outgoing_id,
                        sender_id=authenticated_id,
                        text=reply_text,
                        direction="outgoing",
                    )
                    await session.commit()

            except GeminiAPIError as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "gemini_reply_failed",
                    message_id=data.message_id,
                    model=exc.model,
                    error=str(exc),
                    hint="Set GEMINI_MODEL=gemini-2.5-flash in Render environment variables",
                )

            except InstagramAPIError as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "instagram_send_message_failed",
                    message_id=data.message_id,
                    sender_id=data.sender_id,
                    error=str(exc),
                    status_code=exc.status_code,
                    response_body=exc.response_body,
                )

            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "message_processing_error",
                    message_id=data.message_id,
                    error=str(exc),
                )
