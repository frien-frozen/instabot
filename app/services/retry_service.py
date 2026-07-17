"""Replay pending replies when the app starts after a crash or outage."""

from __future__ import annotations

import logging

from app.config import Settings
from app.database import SessionFactory
from app.schemas import CommentCreate, MentionCreate, MessageCreate
from app.services.comment_processor import CommentProcessor
from app.services.mention_processor import MentionProcessor
from app.services.message_processor import MessageProcessor
from app.services.pending_reply_repository import PendingReplyRepository
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


class RetryService:
    """Drain the pending reply queue on startup."""

    def __init__(
        self,
        settings: Settings,
        session_factory: SessionFactory,
        comment_processor: CommentProcessor,
        message_processor: MessageProcessor,
        mention_processor: MentionProcessor,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._comment_processor = comment_processor
        self._message_processor = message_processor
        self._mention_processor = mention_processor

    async def process_pending_on_startup(self) -> None:
        if not self._settings.retry_on_startup:
            return

        async with self._session_factory() as session:
            repo = PendingReplyRepository(session)
            pending = await repo.list_pending(limit=self._settings.retry_batch_size)

        if not pending:
            log_event(logger, logging.INFO, "pending_replies_empty")
            return

        log_event(
            logger,
            logging.INFO,
            "pending_replies_replay_started",
            count=len(pending),
        )

        for row in pending:
            if row.attempts >= self._settings.max_reply_attempts:
                log_event(
                    logger,
                    logging.WARNING,
                    "pending_reply_max_attempts_reached",
                    event_type=row.event_type,
                    external_event_id=row.external_event_id,
                    attempts=row.attempts,
                )
                continue

            payload = PendingReplyRepository.decode_payload(row)
            try:
                await self._dispatch(row.event_type, payload)
            except Exception as exc:
                async with self._session_factory() as session:
                    repo = PendingReplyRepository(session)
                    await repo.record_failure(row.event_type, row.external_event_id, str(exc))
                    await session.commit()
                log_event(
                    logger,
                    logging.ERROR,
                    "pending_reply_replay_failed",
                    event_type=row.event_type,
                    external_event_id=row.external_event_id,
                    error=str(exc),
                )

        log_event(logger, logging.INFO, "pending_replies_replay_finished")

    async def _dispatch(self, event_type: str, payload: dict) -> None:
        if event_type == "comment":
            await self._comment_processor.process(CommentCreate(**payload), from_retry=True)
            return
        if event_type == "message":
            await self._message_processor.process(MessageCreate(**payload), from_retry=True)
            return
        if event_type == "mention":
            await self._mention_processor.process(MentionCreate(**payload), from_retry=True)
            return
        raise ValueError(f"Unsupported pending event type: {event_type}")
