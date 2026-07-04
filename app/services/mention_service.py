"""Instagram mention automation driven by dashboard-synced profiles."""

from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.schemas.events import MentionEvent
from app.services.ai_service import AIService
from app.services.conversation_service import ConversationService
from app.services.gemini_service import GeminiAPIError
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.services.profile_resolver import ProfileResolver
from app.utils.agent_logging import agent_log
from app.utils.logging import get_logger, log_duration

logger = get_logger(__name__)


class MentionService:
    """Reply to Instagram mentions when enabled and API supports the event."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        profile_resolver: ProfileResolver,
        ai_service: AIService,
        conversation_service: ConversationService,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._profiles = profile_resolver
        self._ai = ai_service
        self._conversation = conversation_service

    async def handle(self, event: MentionEvent) -> None:
        started = time.monotonic()
        with log_duration(logger, "mention_processing", mention_id=event.mention_id):
            profile = await self._profiles.resolve(event.account_external_id)
            if profile is None:
                agent_log(
                    logger,
                    "ERROR",
                    logging.ERROR,
                    "mention rejected because profile was not found",
                    instagram_id=event.account_external_id,
                    mention_id=event.mention_id,
                )
                return
            if not profile.is_feature_enabled("mention", event.mention_type):
                agent_log(
                    logger,
                    "MENTION",
                    logging.INFO,
                    "mention reply disabled for profile",
                    username=profile.username,
                    instagram_id=profile.instagram_id,
                    mention_type=event.mention_type,
                )
                return

            instagram = InstagramService.for_profile(self._settings, profile)
            agent_log(
                logger,
                "MENTION",
                logging.INFO,
                "processing mention event",
                username=profile.username,
                instagram_id=profile.instagram_id,
                mention_id=event.mention_id,
                mention_type=event.mention_type,
            )

            auth_id = await instagram.get_authenticated_user_id()
            if event.from_id and event.from_id == auth_id:
                agent_log(
                    logger,
                    "MENTION",
                    logging.INFO,
                    "ignoring own mention",
                    username=profile.username,
                    mention_id=event.mention_id,
                )
                return

            async with self._session_factory() as session:
                if await self._conversation.is_processed(
                    session,
                    account_id=profile.account_id,
                    platform=event.platform,
                    event_type=event.event_type,
                    external_event_id=event.external_event_id,
                ):
                    return

            incoming_text = event.text or f"Mention ({event.mention_type})"

            if not event.comment_id:
                agent_log(
                    logger,
                    "MENTION",
                    logging.INFO,
                    "mention reply unsupported for event shape",
                    username=profile.username,
                    mention_type=event.mention_type,
                    mention_id=event.mention_id,
                )
                async with self._session_factory() as session:
                    await self._conversation.log_event(
                        session,
                        account_id=profile.account_id,
                        platform=event.platform,
                        event_type=event.event_type,
                        external_event_id=event.external_event_id,
                        external_user_id=event.from_id,
                        username=event.username,
                        incoming_text=incoming_text,
                        api_status="skipped",
                        api_response={"reason": "unsupported_mention_type"},
                        duration_ms=int((time.monotonic() - started) * 1000),
                        metadata={"mention_type": event.mention_type, "media_id": event.media_id},
                    )
                    await session.commit()
                return

            reply_target = event.comment_id

            try:
                async with self._session_factory() as session:
                    reply_text = await self._ai.generate_reply(
                        session,
                        profile,
                        incoming_text,
                        user_id=event.from_id,
                        account_external_id=event.account_external_id,
                    )

                result = await instagram.reply_comment(reply_target, reply_text)

                async with self._session_factory() as session:
                    await self._conversation.mark_processed(
                        session,
                        account_id=profile.account_id,
                        platform=event.platform,
                        event_type=event.event_type,
                        external_event_id=event.external_event_id,
                    )
                    await self._conversation.log_event(
                        session,
                        account_id=profile.account_id,
                        platform=event.platform,
                        event_type=event.event_type,
                        external_event_id=event.external_event_id,
                        external_user_id=event.from_id,
                        username=event.username,
                        incoming_text=incoming_text,
                        generated_reply=reply_text,
                        api_status="success",
                        api_response=result,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        metadata={"mention_type": event.mention_type, "media_id": event.media_id},
                    )
                    await session.commit()
                    agent_log(
                        logger,
                        "MENTION",
                        logging.INFO,
                        "mention reply sent",
                        username=profile.username,
                        mention_id=event.mention_id,
                    )

            except (GeminiAPIError, InstagramAPIError) as exc:
                async with self._session_factory() as session:
                    await self._conversation.log_event(
                        session,
                        account_id=profile.account_id,
                        platform=event.platform,
                        event_type=event.event_type,
                        external_event_id=event.external_event_id,
                        username=event.username,
                        incoming_text=incoming_text,
                        api_status="error",
                        error=str(exc),
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                    await session.commit()
                agent_log(
                    logger,
                    "ERROR",
                    logging.ERROR,
                    "mention reply failed",
                    username=profile.username,
                    mention_id=event.mention_id,
                    error=str(exc),
                )
