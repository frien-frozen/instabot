"""Comment reply automation driven by dashboard-synced profile configuration."""

from __future__ import annotations

import asyncio
import logging
import random
import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.schemas import CommentCreate
from app.schemas.events import CommentEvent
from app.services.ai_service import AIService
from app.services.comment_repository import CommentRepository
from app.services.conversation_service import ConversationService
from app.services.gemini_service import GeminiAPIError
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.services.profile_resolver import ProfileResolver
from app.utils.agent_logging import agent_log
from app.utils.logging import get_logger, log_duration
from app.utils.spam import is_spam

logger = get_logger(__name__)


class CommentService:
    """Process Instagram comment events for a configured account."""

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

    async def process(self, data: CommentCreate) -> None:
        event = CommentEvent(
            platform="instagram",
            event_type="comment",
            account_external_id=data.account_id or "",
            external_event_id=data.comment_id,
            comment_id=data.comment_id,
            username=data.username,
            text=data.message,
            media_id=data.media_id,
            from_id=data.from_id,
            parent_comment_id=data.parent_comment_id,
        )
        await self.handle(event)

    async def handle(self, event: CommentEvent) -> None:
        started = time.monotonic()
        with log_duration(logger, "comment_processing", comment_id=event.comment_id, username=event.username):
            profile = await self._profiles.resolve(event.account_external_id)
            if profile is None:
                agent_log(
                    logger,
                    "ERROR",
                    logging.ERROR,
                    "comment rejected because profile was not found",
                    instagram_id=event.account_external_id,
                    comment_id=event.comment_id,
                )
                return
            if not profile.is_feature_enabled("comment"):
                agent_log(
                    logger,
                    "COMMENT",
                    logging.INFO,
                    "comment reply disabled for profile",
                    username=profile.username,
                    instagram_id=profile.instagram_id,
                    comment_id=event.comment_id,
                )
                return

            instagram = InstagramService.for_profile(self._settings, profile)
            agent_log(
                logger,
                "COMMENT",
                logging.INFO,
                "processing comment event",
                username=profile.username,
                instagram_id=profile.instagram_id,
                comment_id=event.comment_id,
                delay_min=profile.delay_min,
                delay_max=profile.delay_max,
            )

            text = event.text
            username = event.username
            from_id = event.from_id

            try:
                details = await instagram.fetch_comment_details(event.comment_id)
                from_obj = details.get("from") or {}
                if isinstance(from_obj, dict):
                    if from_obj.get("id"):
                        from_id = str(from_obj["id"])
                    if from_obj.get("username"):
                        username = str(from_obj["username"])
                    if details.get("text"):
                        text = str(details["text"])
            except InstagramAPIError as exc:
                async with self._session_factory() as session:
                    await self._conversation.log_event(
                        session,
                        account_id=profile.account_id,
                        platform=event.platform,
                        event_type=event.event_type,
                        external_event_id=event.external_event_id,
                        username=event.username,
                        incoming_text=event.text,
                        api_status="error",
                        error=str(exc),
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                    await session.commit()
                agent_log(
                    logger,
                    "ERROR",
                    logging.ERROR,
                    "comment fetch failed",
                    username=profile.username,
                    comment_id=event.comment_id,
                    error=str(exc),
                )
                return

            auth_id = await instagram.get_authenticated_user_id()
            if from_id and from_id == auth_id:
                agent_log(
                    logger,
                    "COMMENT",
                    logging.INFO,
                    "ignoring own comment",
                    username=profile.username,
                    comment_id=event.comment_id,
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

                repo = CommentRepository(session)
                if await repo.has_been_replied(event.comment_id):
                    return

                data = CommentCreate(
                    comment_id=event.comment_id,
                    username=username,
                    message=text,
                    media_id=event.media_id,
                    from_id=from_id,
                    parent_comment_id=event.parent_comment_id,
                    account_id=event.account_external_id,
                )
                await repo.create(data)

                spam, reason = is_spam(text)
                if spam:
                    agent_log(
                        logger,
                        "COMMENT",
                        logging.INFO,
                        "spam comment ignored",
                        username=profile.username,
                        comment_id=event.comment_id,
                        reason=reason,
                    )
                    await session.commit()
                    return

                delay = random.randint(profile.delay_min, profile.delay_max)
                await session.commit()

            await asyncio.sleep(delay)

            async with self._session_factory() as session:
                repo = CommentRepository(session)
                if await repo.has_been_replied(event.comment_id):
                    await session.commit()
                    return

                try:
                    reply_text = await self._ai.generate_reply(session, profile, text)
                    result = await instagram.reply_comment(event.comment_id, reply_text)
                    await repo.mark_replied(event.comment_id, reply_text)
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
                        external_user_id=from_id,
                        username=username,
                        incoming_text=text,
                        generated_reply=reply_text,
                        api_status="success",
                        api_response=result,
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                    await session.commit()
                    agent_log(
                        logger,
                        "COMMENT",
                        logging.INFO,
                        "comment reply sent",
                        username=profile.username,
                        comment_id=event.comment_id,
                    )
                except (GeminiAPIError, InstagramAPIError) as exc:
                    await session.rollback()
                    async with self._session_factory() as err_session:
                        await self._conversation.log_event(
                            err_session,
                            account_id=profile.account_id,
                            platform=event.platform,
                            event_type=event.event_type,
                            external_event_id=event.external_event_id,
                            username=username,
                            incoming_text=text,
                            api_status="error",
                            error=str(exc),
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                        await err_session.commit()
                    agent_log(
                        logger,
                        "ERROR",
                        logging.ERROR,
                        "comment reply failed",
                        username=profile.username,
                        comment_id=event.comment_id,
                        error=str(exc),
                    )
