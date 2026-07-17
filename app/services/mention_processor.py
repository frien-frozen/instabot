"""Orchestrates Instagram mention processing for a single account."""

from __future__ import annotations

import logging

from app.config import Settings
from app.database import SessionFactory
from app.schemas import MentionCreate
from app.services.gemini_service import GeminiAPIError, GeminiService
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.services.pending_reply_repository import PendingReplyRepository
from app.services.processed_webhook_repository import ProcessedWebhookRepository
from app.utils.logging import get_logger, log_duration, log_event
from app.utils.profile_context import format_profile_context

logger = get_logger(__name__)

EVENT_TYPE = "mention"


class MentionProcessor:
    """Reply to Instagram mentions and story mentions using the configured account."""

    def __init__(
        self,
        settings: Settings,
        session_factory: SessionFactory,
        gemini_service: GeminiService,
        instagram_service: InstagramService,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._gemini = gemini_service
        self._instagram = instagram_service

    async def process(self, data: MentionCreate, *, from_retry: bool = False) -> None:
        with log_duration(
            logger,
            "mention_processing",
            mention_id=data.mention_id,
            mention_type=data.mention_type,
            from_retry=from_retry,
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

            data = await self._enrich_from_api(data)

            async with self._session_factory() as session:
                processed_repo = ProcessedWebhookRepository(session)
                pending_repo = PendingReplyRepository(session)

                if await processed_repo.is_processed("mention", data.mention_id):
                    log_event(
                        logger,
                        logging.INFO,
                        "duplicate_mention_skipped",
                        mention_id=data.mention_id,
                    )
                    await pending_repo.complete(EVENT_TYPE, data.mention_id)
                    await session.commit()
                    return

                await pending_repo.upsert(
                    EVENT_TYPE,
                    data.mention_id,
                    data.model_dump(mode="json"),
                )
                await session.commit()

            if not self._has_reply_target(data):
                log_event(
                    logger,
                    logging.INFO,
                    "mention_reply_unsupported",
                    mention_id=data.mention_id,
                    mention_type=data.mention_type,
                    reason="no_reply_target",
                )
                async with self._session_factory() as session:
                    await PendingReplyRepository(session).complete(EVENT_TYPE, data.mention_id)
                    await session.commit()
                return

            incoming_text = data.text or f"Mention ({data.mention_type})"
            profile_context = await self._build_profile_context(data)

            try:
                reply_text = await self._gemini.generate_reply(
                    incoming_text,
                    personality_override=self._settings.resolved_system_prompt or None,
                    profile_context=profile_context or None,
                )
                await self._deliver_reply(data, reply_text)

                async with self._session_factory() as session:
                    await ProcessedWebhookRepository(session).mark_processed(
                        "mention",
                        data.mention_id,
                    )
                    await PendingReplyRepository(session).complete(EVENT_TYPE, data.mention_id)
                    await session.commit()

                log_event(
                    logger,
                    logging.INFO,
                    "mention_reply_success",
                    mention_id=data.mention_id,
                    mention_type=data.mention_type,
                    comment_id=data.comment_id,
                    reply_text=reply_text,
                )
            except GeminiAPIError as exc:
                await self._record_failure(data, str(exc))
                log_event(
                    logger,
                    logging.ERROR,
                    "gemini_reply_failed",
                    mention_id=data.mention_id,
                    model=exc.model,
                    error=str(exc),
                )
            except InstagramAPIError as exc:
                await self._record_failure(data, str(exc))
                log_event(
                    logger,
                    logging.ERROR,
                    "mention_reply_failed",
                    mention_id=data.mention_id,
                    error=str(exc),
                    status_code=exc.status_code,
                )
            except Exception as exc:
                await self._record_failure(data, str(exc))
                log_event(
                    logger,
                    logging.ERROR,
                    "mention_processing_error",
                    mention_id=data.mention_id,
                    error=str(exc),
                )

    async def _enrich_from_api(self, data: MentionCreate) -> MentionCreate:
        """Resolve comment id and text from Graph API for comment mentions."""
        if data.mention_type == "story_mentions":
            return data

        candidates: list[str] = []
        if data.comment_id:
            candidates.append(data.comment_id)
        if data.mention_id and data.mention_id not in candidates:
            candidates.append(data.mention_id)

        for candidate in candidates:
            try:
                details = await self._instagram.get_comment(candidate)
                log_event(
                    logger,
                    logging.INFO,
                    "mention_comment_resolved",
                    mention_id=data.mention_id,
                    comment_id=candidate,
                    details=details,
                )
                data.comment_id = str(details.get("id") or candidate)
                if details.get("text"):
                    data.text = str(details["text"])
                from_obj = details.get("from") or {}
                if isinstance(from_obj, dict):
                    if from_obj.get("id"):
                        data.from_id = str(from_obj["id"])
                    if from_obj.get("username"):
                        data.username = str(from_obj["username"])
                return data
            except InstagramAPIError as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "mention_comment_resolve_failed",
                    mention_id=data.mention_id,
                    candidate_id=candidate,
                    error=str(exc),
                    status_code=exc.status_code,
                )

        return data

    async def _build_profile_context(self, data: MentionCreate) -> str:
        if not self._settings.profile_context_enabled or not data.from_id:
            return ""
        profile = await self._instagram.fetch_user_profile(
            data.from_id,
            fallback_username=data.username,
        )
        return format_profile_context(profile)

    @staticmethod
    def _has_reply_target(data: MentionCreate) -> bool:
        if data.mention_type == "story_mentions":
            return bool(data.from_id)
        if data.comment_id:
            return True
        return bool(data.from_id)

    async def _deliver_reply(self, data: MentionCreate, reply_text: str) -> None:
        """Route mention replies to the correct Instagram API endpoint."""
        if data.mention_type == "story_mentions":
            if not data.from_id:
                raise InstagramAPIError("Story mention missing sender id")
            await self._instagram.send_message(data.from_id, reply_text)
            return

        if data.mention_type == "mentions" and data.comment_id:
            try:
                await self._instagram.send_private_reply_to_comment(data.comment_id, reply_text)
                return
            except InstagramAPIError as private_exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "mention_private_reply_failed_trying_public",
                    mention_id=data.mention_id,
                    comment_id=data.comment_id,
                    error=str(private_exc),
                )
                await self._instagram.reply_comment(data.comment_id, reply_text)
                return

        if data.comment_id:
            await self._instagram.reply_comment(data.comment_id, reply_text)
            return

        if data.from_id:
            await self._instagram.send_message(data.from_id, reply_text)
            return

        raise InstagramAPIError("Mention has no reply target")

    async def _record_failure(self, data: MentionCreate, error: str) -> None:
        async with self._session_factory() as session:
            await PendingReplyRepository(session).record_failure(
                EVENT_TYPE,
                data.mention_id,
                error,
            )
            await session.commit()
