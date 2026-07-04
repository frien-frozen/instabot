"""Orchestrates comment processing: spam filter, delay, AI reply, Instagram post."""

import asyncio
import logging
import random

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.schemas import CommentCreate
from app.services.comment_repository import CommentRepository
from app.services.gemini_service import GeminiService
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.utils.logging import get_logger, log_duration, log_event
from app.utils.spam import is_spam

logger = get_logger(__name__)


class CommentProcessor:
    """
    End-to-end comment processing pipeline.

    Pipeline steps:
      1. Duplicate check
      2. Spam detection
      3. Random human-like delay
      4. Gemini reply generation
      5. Instagram API reply
      6. Persist reply state

    Runs asynchronously after webhook acknowledgment to keep Meta happy.
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

    async def process(self, data: CommentCreate) -> None:
        """Process a single incoming comment through the full pipeline."""
        with log_duration(
            logger,
            "comment_processing",
            comment_id=data.comment_id,
            username=data.username,
        ):
            async with self._session_factory() as session:
                repo = CommentRepository(session)

                # Duplicate protection — never reply twice
                if await repo.has_been_replied(data.comment_id):
                    log_event(
                        logger,
                        logging.INFO,
                        "duplicate_reply_skipped",
                        comment_id=data.comment_id,
                    )
                    return

                comment = await repo.create(data)
                if comment is None:
                    existing = await repo.get_by_comment_id(data.comment_id)
                    if existing is None or existing.replied:
                        return
                    comment = existing

                # Spam detection
                spam, reason = is_spam(data.message)
                if spam:
                    log_event(
                        logger,
                        logging.INFO,
                        "spam_comment_ignored",
                        comment_id=data.comment_id,
                        reason=reason,
                        comment_text=data.message,
                    )
                    await session.commit()
                    return

                log_event(
                    logger,
                    logging.INFO,
                    "incoming_comment",
                    comment_id=data.comment_id,
                    username=data.username,
                    comment_text=data.message,
                    media_id=data.media_id,
                    parent_comment_id=data.parent_comment_id,
                )

                # Random delay for natural appearance
                delay = random.randint(
                    self._settings.reply_delay_min_seconds,
                    self._settings.reply_delay_max_seconds,
                )
                log_event(
                    logger,
                    logging.INFO,
                    "reply_delay_started",
                    comment_id=data.comment_id,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)

                # Re-check duplicate after delay (race condition guard)
                if await repo.has_been_replied(data.comment_id):
                    log_event(
                        logger,
                        logging.INFO,
                        "duplicate_reply_skipped_after_delay",
                        comment_id=data.comment_id,
                    )
                    await session.commit()
                    return

                try:
                    reply_text = await self._gemini.generate_reply(data.message)
                    await self._instagram.reply_comment(data.comment_id, reply_text)
                    await repo.mark_replied(data.comment_id, reply_text)
                    await session.commit()

                except InstagramAPIError as exc:
                    await session.rollback()
                    log_event(
                        logger,
                        logging.ERROR,
                        "instagram_reply_failed",
                        comment_id=data.comment_id,
                        error=str(exc),
                        status_code=exc.status_code,
                    )

                except Exception as exc:
                    await session.rollback()
                    log_event(
                        logger,
                        logging.ERROR,
                        "comment_processing_error",
                        comment_id=data.comment_id,
                        error=str(exc),
                    )
