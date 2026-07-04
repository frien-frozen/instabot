"""Instagram mention automation (feed, reel, story, post)."""

from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.schemas.events import MentionEvent
from app.services.account_service import AccountService
from app.services.ai_service import AIService
from app.services.conversation_service import ConversationService
from app.services.gemini_service import GeminiAPIError
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.utils.logging import get_logger, log_duration, log_event

logger = get_logger(__name__)


class MentionService:
    """Reply to Instagram mentions when enabled and API supports the event."""

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

    async def handle(self, event: MentionEvent) -> None:
        started = time.monotonic()
        with log_duration(logger, "mention_processing", mention_id=event.mention_id):
            account = await self._accounts.resolve_account(event.account_external_id)
            if account is None:
                return
            if not account.mentions_enabled:
                log_event(logger, logging.INFO, "mentions_disabled", account_id=account.instagram_user_id)
                return

            instagram = InstagramService.for_account(self._settings, account)
            auth_id = await instagram.get_authenticated_user_id()
            if event.from_id and event.from_id == auth_id:
                log_event(logger, logging.INFO, "ignoring_own_mention", mention_id=event.mention_id)
                return

            async with self._session_factory() as session:
                if await self._conversation.is_processed(
                    session,
                    account_id=account.id,
                    platform=event.platform,
                    event_type=event.event_type,
                    external_event_id=event.external_event_id,
                ):
                    return

            incoming_text = event.text or f"Mention ({event.mention_type})"

            if not event.comment_id:
                log_event(
                    logger,
                    logging.INFO,
                    "mention_reply_unsupported",
                    mention_type=event.mention_type,
                    mention_id=event.mention_id,
                    reason="no_comment_id_for_reply",
                )
                async with self._session_factory() as session:
                    await self._conversation.log_event(
                        session,
                        account_id=account.id,
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
                        account,
                        incoming_text,
                        user_id=event.from_id,
                        account_external_id=event.account_external_id,
                    )

                result = await instagram.reply_comment(reply_target, reply_text)
                api_status = "success"

                async with self._session_factory() as session:
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
                        external_user_id=event.from_id,
                        username=event.username,
                        incoming_text=incoming_text,
                        generated_reply=reply_text,
                        api_status=api_status,
                        api_response=result,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        metadata={"mention_type": event.mention_type, "media_id": event.media_id},
                    )
                    await session.commit()

            except (GeminiAPIError, InstagramAPIError) as exc:
                async with self._session_factory() as session:
                    await self._conversation.log_event(
                        session,
                        account_id=account.id,
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
                log_event(logger, logging.ERROR, "mention_reply_failed", mention_id=event.mention_id, error=str(exc))
