"""Instagram Direct Message automation driven by dashboard-synced profiles."""

from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.schemas import MessageCreate
from app.schemas.events import MessageEvent
from app.services.ai_service import AIService
from app.services.conversation_service import ConversationService
from app.services.gemini_service import GeminiAPIError
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.services.message_repository import MessageRepository
from app.services.profile_resolver import ProfileResolver
from app.utils.agent_logging import agent_log
from app.utils.logging import get_logger, log_duration

logger = get_logger(__name__)


class MessageService:
    """Process Instagram DM events with conversation memory."""

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

    async def process(self, data: MessageCreate) -> None:
        event = MessageEvent(
            platform="instagram",
            event_type="message",
            account_external_id=data.account_id or "",
            external_event_id=data.message_id,
            message_id=data.message_id,
            sender_id=data.sender_id,
            recipient_id=data.recipient_id,
            text=data.text,
            timestamp=data.timestamp,
            is_echo=data.is_echo,
        )
        await self.handle(event)

    async def handle(self, event: MessageEvent) -> None:
        started = time.monotonic()
        with log_duration(logger, "message_processing", message_id=event.message_id, sender_id=event.sender_id):
            if event.is_echo or not event.text.strip():
                return

            profile = await self._profiles.resolve(event.account_external_id)
            if profile is None:
                agent_log(
                    logger,
                    "ERROR",
                    logging.ERROR,
                    "message rejected because profile was not found",
                    instagram_id=event.account_external_id,
                    message_id=event.message_id,
                )
                return
            if not profile.is_feature_enabled("message"):
                agent_log(
                    logger,
                    "MESSAGE",
                    logging.INFO,
                    "message reply disabled for profile",
                    username=profile.username,
                    instagram_id=profile.instagram_id,
                    message_id=event.message_id,
                )
                return

            instagram = InstagramService.for_profile(self._settings, profile)
            agent_log(
                logger,
                "MESSAGE",
                logging.INFO,
                "processing message event",
                username=profile.username,
                instagram_id=profile.instagram_id,
                message_id=event.message_id,
            )

            auth_id = await instagram.get_authenticated_user_id()

            if event.sender_id == auth_id:
                agent_log(
                    logger,
                    "MESSAGE",
                    logging.INFO,
                    "ignoring own message",
                    username=profile.username,
                    message_id=event.message_id,
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

                repo = MessageRepository(session)
                conversation = await repo.get_or_create_conversation(
                    user_id=event.sender_id,
                    account_id=event.account_external_id,
                )
                incoming = await repo.store_message(
                    conversation,
                    message_id=event.message_id,
                    sender_id=event.sender_id,
                    text=event.text,
                    direction="incoming",
                    timestamp=self._conversation.timestamp_from_ms(event.timestamp),
                )
                if incoming is None:
                    await session.commit()
                    return
                await session.commit()

            try:
                async with self._session_factory() as session:
                    reply_text = await self._ai.generate_reply(
                        session,
                        profile,
                        event.text,
                        user_id=event.sender_id,
                        account_external_id=event.account_external_id,
                    )

                result = await instagram.send_message(event.sender_id, reply_text)

                async with self._session_factory() as session:
                    repo = MessageRepository(session)
                    conversation = await repo.get_or_create_conversation(
                        user_id=event.sender_id,
                        account_id=event.account_external_id,
                    )
                    outgoing_id = str(
                        result.get("message_id") or result.get("id") or f"out_{event.message_id}"
                    )
                    await repo.store_message(
                        conversation,
                        message_id=outgoing_id,
                        sender_id=auth_id,
                        text=reply_text,
                        direction="outgoing",
                    )
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
                        external_user_id=event.sender_id,
                        incoming_text=event.text,
                        generated_reply=reply_text,
                        api_status="success",
                        api_response=result,
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                    await session.commit()
                    agent_log(
                        logger,
                        "MESSAGE",
                        logging.INFO,
                        "message reply sent",
                        username=profile.username,
                        message_id=event.message_id,
                    )

            except (GeminiAPIError, InstagramAPIError) as exc:
                async with self._session_factory() as session:
                    await self._conversation.log_event(
                        session,
                        account_id=profile.account_id,
                        platform=event.platform,
                        event_type=event.event_type,
                        external_event_id=event.external_event_id,
                        external_user_id=event.sender_id,
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
                    "message reply failed",
                    username=profile.username,
                    message_id=event.message_id,
                    error=str(exc),
                )
