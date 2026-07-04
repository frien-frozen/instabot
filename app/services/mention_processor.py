"""Orchestrates Instagram mention processing for a single account."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.schemas import MentionCreate
from app.services.gemini_service import GeminiAPIError, GeminiService
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.services.processed_webhook_repository import ProcessedWebhookRepository
from app.utils.logging import get_logger, log_duration, log_event

logger = get_logger(__name__)


class MentionProcessor:
    """Reply to Instagram mentions and story mentions using the configured account."""

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

    async def process(self, data: MentionCreate) -> None:
        with log_duration(
            logger,
            "mention_processing",
            mention_id=data.mention_id,
            mention_type=data.mention_type,
        ):
            if data.mention_type == "story_mentions" and not self._settings.story_mentions_enabled:
                log_event(logger, logging.INFO, "story_mentions_disabled", mention_id=data.mention_id)
                return
            if data.mention_type != "story_mentions" and not self._settings.mentions_enabled:
                log_event(logger, logging.INFO, "mentions_disabled", mention_id=data.mention_id)
                return

            authenticated_id = await self._instagram.get_authenticated_user_id()
            if data.from_id and data.from_id == authenticated_id:
                log_event(logger, logging.INFO, "ignoring_own_mention", mention_id=data.mention_id)
                return

            if not data.comment_id:
                log_event(
                    logger,
                    logging.INFO,
                    "mention_reply_unsupported",
                    mention_id=data.mention_id,
                    mention_type=data.mention_type,
                    reason="no_comment_id_for_reply",
                )
                return

            async with self._session_factory() as session:
                repo = ProcessedWebhookRepository(session)
                if await repo.is_processed("mention", data.mention_id):
                    log_event(logger, logging.INFO, "duplicate_mention_skipped", mention_id=data.mention_id)
                    return

            incoming_text = data.text or f"Mention ({data.mention_type})"

            try:
                reply_text = await self._gemini.generate_reply(
                    incoming_text,
                    personality_override=self._settings.resolved_system_prompt or None,
                )
                await self._instagram.reply_comment(data.comment_id, reply_text)

                async with self._session_factory() as session:
                    repo = ProcessedWebhookRepository(session)
                    await repo.mark_processed("mention", data.mention_id)
                    await session.commit()

                log_event(
                    logger,
                    logging.INFO,
                    "mention_reply_success",
                    mention_id=data.mention_id,
                    mention_type=data.mention_type,
                    reply_text=reply_text,
                )
            except GeminiAPIError as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "gemini_reply_failed",
                    mention_id=data.mention_id,
                    model=exc.model,
                    error=str(exc),
                )
            except InstagramAPIError as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "mention_reply_failed",
                    mention_id=data.mention_id,
                    error=str(exc),
                    status_code=exc.status_code,
                )
