"""Instagram Direct Message automation."""

from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.schemas import MessageCreate
from app.schemas.events import MessageEvent
from app.services.account_service import AccountService
from app.services.ai_service import AIService
from app.services.conversation_service import ConversationService
from app.services.gemini_service import GeminiAPIError
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.services.message_repository import MessageRepository
from app.utils.logging import get_logger, log_duration, log_event

logger = get_logger(__name__)


class MessageService:
    """Process Instagram DM events with conversation memory."""

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

            account = await self._accounts.resolve_account(event.account_external_id)
            if account is None:
                return
            if not account.messages_enabled:
                log_event(logger, logging.INFO, "messages_disabled", account_id=account.instagram_user_id)
                return

            instagram = InstagramService.for_account(self._settings, account)
            auth_id = await instagram.get_authenticated_user_id()

            if event.sender_id == auth_id:
                log_event(logger, logging.INFO, "ignoring_own_message", message_id=event.message_id)
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
                        account,
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
                        external_user_id=event.sender_id,
                        incoming_text=event.text,
                        generated_reply=reply_text,
                        api_status="success",
                        api_response=result,
                        duration_ms=int((time.monotonic() - started) * 1000),
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
                        external_user_id=event.sender_id,
                        incoming_text=event.text,
                        api_status="error",
                        error=str(exc),
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                    await session.commit()
                log_event(logger, logging.ERROR, "message_reply_failed", message_id=event.message_id, error=str(exc))
