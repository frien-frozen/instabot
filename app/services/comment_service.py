"""Comment reply automation — database-driven multi-account pipeline."""

from __future__ import annotations

import asyncio
import logging
import random
import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.schemas import CommentCreate
from app.schemas.events import CommentEvent
from app.services.account_service import AccountService
from app.services.ai_service import AIService
from app.services.comment_repository import CommentRepository
from app.services.conversation_service import ConversationService
from app.services.gemini_service import GeminiAPIError
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.utils.logging import get_logger, log_duration, log_event
from app.utils.spam import is_spam

logger = get_logger(__name__)


class CommentService:
    """Process Instagram comment events for a configured account."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        account_service: AccountService,
        ai_service: AIService,
        conversation_service: ConversationService,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._accounts = account_service
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
            account = await self._accounts.resolve_account(event.account_external_id)
            if account is None:
                log_event(logger, logging.ERROR, "comment_account_not_found", account_id=event.account_external_id)
                return
            if not account.comments_enabled:
                log_event(logger, logging.INFO, "comments_disabled", account_id=account.instagram_user_id)
                return

            instagram = InstagramService.for_account(self._settings, account)

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
                        account_id=account.id,
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
                return

            auth_id = await instagram.get_authenticated_user_id()
            if from_id and from_id == auth_id:
                log_event(logger, logging.INFO, "ignoring_own_comment", comment_id=event.comment_id)
                return

            async with self._session_factory() as session:
                if await self._conversation.is_processed(
                    session,
                    account_id=account.id,
                    platform=event.platform,
                    event_type=event.event_type,
                    external_event_id=event.external_event_id,
                ):
                    log_event(logger, logging.INFO, "duplicate_comment_skipped", comment_id=event.comment_id)
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
                    log_event(logger, logging.INFO, "spam_comment_ignored", comment_id=event.comment_id, reason=reason)
                    await session.commit()
                    return

                delay = random.randint(account.reply_delay_min, account.reply_delay_max)
                log_event(logger, logging.INFO, "reply_delay_started", comment_id=event.comment_id, delay_seconds=delay)
                await session.commit()

            await asyncio.sleep(delay)

            async with self._session_factory() as session:
                repo = CommentRepository(session)
                if await repo.has_been_replied(event.comment_id):
                    await session.commit()
                    return

                try:
                    reply_text = await self._ai.generate_reply(session, account, text)
                    result = await instagram.reply_comment(event.comment_id, reply_text)
                    await repo.mark_replied(event.comment_id, reply_text)
                    await self._conversation.mark_processed(
                        session,
                        account_id=account.id,
                        platform=event.platform,
                        event_type=event.event_type,
                        external_event_id=event.external_event_id,
                    )
                    await self._conversation.log_event(
                        session,
                        account_id=account.id,
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
                    log_event(
                        logger,
                        logging.INFO,
                        "comment_reply_success",
                        comment_id=event.comment_id,
                        reply_text=reply_text,
                    )
                except (GeminiAPIError, InstagramAPIError) as exc:
                    await session.rollback()
                    async with self._session_factory() as err_session:
                        await self._conversation.log_event(
                            err_session,
                            account_id=account.id,
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
                    log_event(logger, logging.ERROR, "comment_reply_failed", comment_id=event.comment_id, error=str(exc))
