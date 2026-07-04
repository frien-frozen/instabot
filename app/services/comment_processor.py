"""Orchestrates comment processing: spam filter, delay, AI reply, Instagram post."""

import asyncio
import logging
import random

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.schemas import CommentCreate
from app.services.comment_repository import CommentRepository
from app.services.gemini_service import GeminiAPIError, GeminiService
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.utils.logging import get_logger, log_duration, log_event
from app.utils.spam import is_spam

logger = get_logger(__name__)


class CommentProcessor:
    """
    End-to-end comment processing pipeline.

    Pipeline steps:
      1. Fetch comment from Graph API (validation + logging)
      2. Duplicate check
      3. Ignore own comments (prevent reply loops)
      4. Spam detection
      5. Random human-like delay
      6. Gemini reply generation
      7. Instagram API reply
      8. Persist reply state
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
            if not self._settings.comments_enabled:
                log_event(logger, logging.INFO, "comments_disabled", comment_id=data.comment_id)
                return

            authenticated_id = await self._instagram.get_authenticated_user_id()
            prompt_override = self._settings.resolved_system_prompt or None

            # Validate comment via Graph API before any filtering
            try:
                comment_details = await self._instagram.fetch_comment_details(data.comment_id)
                log_event(
                    logger,
                    logging.INFO,
                    "comment_fetch_validated",
                    comment_id=data.comment_id,
                    details=comment_details,
                )
                from_obj = comment_details.get("from") or {}
                if isinstance(from_obj, dict):
                    if from_obj.get("id"):
                        data.from_id = str(from_obj["id"])
                    if from_obj.get("username"):
                        data.username = str(from_obj["username"])
                    if comment_details.get("text"):
                        data.message = str(comment_details["text"])
            except InstagramAPIError as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "comment_fetch_failed",
                    comment_id=data.comment_id,
                    status_code=exc.status_code,
                    error=str(exc),
                    response_body=exc.response_body,
                )
                return

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

                # Ignore comments from our own account (prevents infinite loops)
                comment_author_id = data.from_id or ""
                if comment_author_id and comment_author_id == authenticated_id:
                    log_event(
                        logger,
                        logging.INFO,
                        "ignoring_own_comment",
                        comment_id=data.comment_id,
                        from_id=comment_author_id,
                        authenticated_user_id=authenticated_id,
                        username=data.username,
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
                    from_id=data.from_id,
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

                # Re-check duplicate and own-comment after delay
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
                    reply_text = await self._gemini.generate_reply(
                        data.message,
                        personality_override=prompt_override,
                    )
                    await self._instagram.reply_comment(data.comment_id, reply_text)
                    await repo.mark_replied(data.comment_id, reply_text)
                    await session.commit()

                    log_event(
                        logger,
                        logging.INFO,
                        "comment_reply_success",
                        comment_id=data.comment_id,
                        username=data.username,
                        reply_text=reply_text,
                        media_id=data.media_id,
                    )

                except GeminiAPIError as exc:
                    await session.rollback()
                    log_event(
                        logger,
                        logging.ERROR,
                        "gemini_reply_failed",
                        comment_id=data.comment_id,
                        model=exc.model,
                        error=str(exc),
                        hint="Set GEMINI_MODEL=gemini-2.5-flash in Render environment variables",
                    )

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
