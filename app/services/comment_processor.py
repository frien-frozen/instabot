"""Orchestrates comment processing: spam filter, delay, AI reply, Instagram post."""

from __future__ import annotations

import asyncio
import logging
import random

from app.config import Settings
from app.database import SessionFactory
from app.gemini_config import DEFAULT_GEMINI_MODEL
from app.models.media import Media
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.media_repository import MediaRepository
from app.schemas import CommentCreate
from app.services.comment_repository import CommentRepository
from app.services.gemini_service import GeminiAPIError, GeminiService
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.services.message_repository import MessageRepository
from app.services.pending_reply_repository import PendingReplyRepository
from app.utils.comment_context import (
    CampaignPlan,
    build_campaign_followup_dm,
    build_comment_context_package,
    classify_post_intent,
    extract_caption_triggers,
)
from app.utils.comment_intent import classify_comment_intent_fast, pick_supportive_reply
from app.utils.logging import get_logger, log_duration, log_event
from app.utils.profile_context import format_profile_context
from app.utils.spam import is_spam

logger = get_logger(__name__)

EVENT_TYPE = "comment"


class CommentProcessor:
    """
    End-to-end comment processing pipeline (retry / legacy path).

    Production comments go through the event queue → CommentTaskHandler.
    This processor mirrors the same media cache + campaign + post-context flow.
    """

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

    async def process(self, data: CommentCreate, *, from_retry: bool = False) -> None:
        """Process a single incoming comment through the full pipeline."""
        with log_duration(
            logger,
            "comment_processing",
            comment_id=data.comment_id,
            username=data.username,
            from_retry=from_retry,
        ):
            if not self._settings.comments_enabled:
                log_event(logger, logging.INFO, "comments_disabled", comment_id=data.comment_id)
                return

            authenticated_id = await self._instagram.get_authenticated_user_id()
            prompt_override = self._settings.resolved_system_prompt or None

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
                media_obj = comment_details.get("media") or {}
                if isinstance(media_obj, dict) and media_obj.get("id") and not data.media_id:
                    data.media_id = str(media_obj["id"])
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
                await self._record_failure(data, str(exc))
                return

            async with self._session_factory() as session:
                repo = CommentRepository(session)
                pending_repo = PendingReplyRepository(session)

                if await repo.has_been_replied(data.comment_id):
                    log_event(
                        logger,
                        logging.INFO,
                        "duplicate_reply_skipped",
                        comment_id=data.comment_id,
                    )
                    await pending_repo.complete(EVENT_TYPE, data.comment_id)
                    await session.commit()
                    return

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
                    await pending_repo.complete(EVENT_TYPE, data.comment_id)
                    await session.commit()
                    return

                comment = await repo.create(data)
                if comment is None:
                    existing = await repo.get_by_comment_id(data.comment_id)
                    if existing is None or existing.replied:
                        await pending_repo.complete(EVENT_TYPE, data.comment_id)
                        await session.commit()
                        return

                await pending_repo.upsert(
                    EVENT_TYPE,
                    data.comment_id,
                    data.model_dump(mode="json"),
                )
                await session.commit()

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
                async with self._session_factory() as session:
                    await PendingReplyRepository(session).complete(EVENT_TYPE, data.comment_id)
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

            if not from_retry:
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

            async with self._session_factory() as session:
                repo = CommentRepository(session)
                if await repo.has_been_replied(data.comment_id):
                    log_event(
                        logger,
                        logging.INFO,
                        "duplicate_reply_skipped_after_delay",
                        comment_id=data.comment_id,
                    )
                    await PendingReplyRepository(session).complete(EVENT_TYPE, data.comment_id)
                    await session.commit()
                    return

            try:
                media = await self._get_or_fetch_media(data.media_id)
                campaign = await self._resolve_campaign(data.message, data.media_id, media)

                if campaign is not None:
                    await self._handle_campaign(data, campaign)
                    async with self._session_factory() as session:
                        await PendingReplyRepository(session).complete(EVENT_TYPE, data.comment_id)
                        await session.commit()
                    return

                comment_intent = classify_comment_intent_fast(data.message)
                if comment_intent is None:
                    post_context_for_class = None
                    if media is not None:
                        post_context_for_class = (
                            f"Post caption:\n{media.caption or '(none)'}\n"
                            f"Post intent: {media.intent}"
                        )
                    comment_intent = await self._gemini.classify_comment_intent(
                        data.message,
                        post_context=post_context_for_class,
                    )

                if comment_intent == "Spam":
                    log_event(
                        logger,
                        logging.INFO,
                        "comment_intent_spam_skipped",
                        comment_id=data.comment_id,
                        message=data.message,
                    )
                    async with self._session_factory() as session:
                        await PendingReplyRepository(session).complete(EVENT_TYPE, data.comment_id)
                        await session.commit()
                    return

                if comment_intent == "Supportive":
                    reply_text = pick_supportive_reply(data.message)
                    await self._instagram.reply_comment(data.comment_id, reply_text)
                    async with self._session_factory() as session:
                        repo = CommentRepository(session)
                        await repo.mark_replied(data.comment_id, reply_text)
                        await PendingReplyRepository(session).complete(EVENT_TYPE, data.comment_id)
                        await session.commit()
                    log_event(
                        logger,
                        logging.INFO,
                        "comment_supportive_reply_success",
                        comment_id=data.comment_id,
                        reply_text=reply_text,
                        intent=comment_intent,
                    )
                    return

                profile_context, display_name = await self._build_profile_context(data)
                memory_context, previous_comments = await self._load_memory(data)
                post_context = build_comment_context_package(
                    media,
                    data.message,
                    data.comment_id,
                    username=data.username,
                    display_name=display_name,
                    from_id=data.from_id,
                    memory_context=memory_context,
                    previous_comments=previous_comments,
                    campaign=None,
                    comment_intent=comment_intent,
                )
                reply_text = await self._gemini.generate_reply(
                    data.message,
                    personality_override=prompt_override,
                    profile_context=profile_context,
                    memory_context=memory_context,
                    post_context=post_context,
                )
                await self._instagram.reply_comment(data.comment_id, reply_text)

                async with self._session_factory() as session:
                    repo = CommentRepository(session)
                    await repo.mark_replied(data.comment_id, reply_text)
                    await PendingReplyRepository(session).complete(EVENT_TYPE, data.comment_id)
                    await session.commit()

                log_event(
                    logger,
                    logging.INFO,
                    "comment_reply_success",
                    comment_id=data.comment_id,
                    username=data.username,
                    reply_text=reply_text,
                    media_id=data.media_id,
                    post_intent=media.intent if media else None,
                    comment_intent=comment_intent,
                )

            except GeminiAPIError as exc:
                await self._record_failure(data, str(exc))
                log_event(
                    logger,
                    logging.ERROR,
                    "gemini_reply_failed",
                    comment_id=data.comment_id,
                    model=exc.model,
                    error=str(exc),
                    hint=f"Set GEMINI_MODEL={DEFAULT_GEMINI_MODEL} in Render environment variables",
                )

            except InstagramAPIError as exc:
                await self._record_failure(data, str(exc))
                log_event(
                    logger,
                    logging.ERROR,
                    "instagram_reply_failed",
                    comment_id=data.comment_id,
                    error=str(exc),
                    status_code=exc.status_code,
                )

            except Exception as exc:
                await self._record_failure(data, str(exc))
                log_event(
                    logger,
                    logging.ERROR,
                    "comment_processing_error",
                    comment_id=data.comment_id,
                    error=str(exc),
                )

    async def _get_or_fetch_media(self, media_id: str) -> Media | None:
        if not media_id:
            return None
        async with self._session_factory() as session:
            cached = await MediaRepository(session).get_by_media_id(media_id)
            if cached is not None:
                return cached
        try:
            payload = await self._instagram.get_media(media_id)
        except Exception as exc:
            log_event(logger, logging.WARNING, "media_fetch_failed", media_id=media_id, error=str(exc))
            return None
        caption = str(payload.get("caption") or "")
        intent = classify_post_intent(caption)
        async with self._session_factory() as session:
            media = await MediaRepository(session).upsert_from_graph(
                media_id=media_id,
                payload=payload,
                intent=intent,
            )
            await session.commit()
        log_event(
            logger,
            logging.INFO,
            "media_cached",
            media_id=media_id,
            intent=intent,
            caption_chars=len(caption),
            triggers=extract_caption_triggers(caption),
        )
        return media

    async def _resolve_campaign(
        self,
        comment_text: str,
        media_id: str,
        media: Media | None,
    ) -> CampaignPlan | None:
        async with self._session_factory() as session:
            matched = await CampaignRepository(session).match_comment(
                comment_text=comment_text,
                media_id=media_id or None,
            )
            if matched is not None:
                return CampaignPlan.from_document(matched)
        if media and media.caption:
            triggers = extract_caption_triggers(media.caption)
            text = (comment_text or "").strip().lower()
            for trigger in triggers:
                # Exact keyword match only — longer questions go to Gemini.
                if text == trigger.lower():
                    return CampaignPlan(
                        name=f"auto:{media.media_id}:{trigger}",
                        media_id=media.media_id,
                        goal="lead_magnet",
                        intent="lead_magnet",
                        trigger_keywords=[trigger],
                        public_reply="Rahmat! Ma'lumot DM'ingizga yuborildi 📩",
                        dm_text=(
                            f"Assalomu alaykum! Siz \"{trigger}\" deb yozgansiz.\n\n"
                            "Qo'llanma / ro'yxat shu yerda. Savolingiz bo'lsa yozing — "
                            "men doktor Sultonbekning administrator yordamchisiman."
                        ),
                        ask_name_after_dm=True,
                        ask_phone_after_dm=True,
                        offer_consultation=True,
                    )
        return None

    async def _handle_campaign(self, data: CommentCreate, campaign: CampaignPlan) -> None:
        public_reply = (campaign.public_reply or "Rahmat! DM'ingizga yubordik 📩").strip()
        await self._instagram.reply_comment(data.comment_id, public_reply)
        dm_text = build_campaign_followup_dm(campaign)
        if dm_text and data.from_id:
            try:
                await self._instagram.send_private_reply_to_comment(data.comment_id, dm_text)
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "campaign_private_reply_failed",
                    comment_id=data.comment_id,
                    error=str(exc),
                )
                try:
                    await self._instagram.send_message(data.from_id, dm_text)
                except Exception as exc2:
                    log_event(
                        logger,
                        logging.ERROR,
                        "campaign_dm_failed",
                        comment_id=data.comment_id,
                        error=str(exc2),
                    )
        async with self._session_factory() as session:
            await CommentRepository(session).mark_replied(data.comment_id, public_reply)
            await session.commit()
        log_event(
            logger,
            logging.INFO,
            "campaign_comment_handled",
            comment_id=data.comment_id,
            campaign_name=campaign.name,
            media_id=data.media_id,
            trigger=data.message,
        )

    async def _build_profile_context(self, data: CommentCreate) -> tuple[str | None, str]:
        if not self._settings.profile_context_enabled or not data.from_id:
            return None, ""
        profile = await self._instagram.fetch_user_profile(
            data.from_id,
            fallback_username=data.username,
        )
        return format_profile_context(profile) or None, str(profile.get("name") or "")

    async def _load_memory(self, data: CommentCreate) -> tuple[str | None, str | None]:
        memory_context = None
        previous_comments = None
        bot_id = await self._instagram.get_authenticated_user_id()
        async with self._session_factory() as session:
            if data.from_id:
                conv = await MessageRepository(session).get_conversation_by_user(
                    data.from_id,
                    account_id=data.account_id,
                )
                if conv and conv.id is not None:
                    memory_context = (
                        await MessageRepository(session).build_conversation_history(
                            conv.id,
                            bot_user_id=bot_id,
                            limit=self._settings.dm_history_limit,
                        )
                        or None
                    )
            prior = await CommentRepository(session).recent_by_user(
                from_id=data.from_id,
                username=data.username,
                limit=5,
                exclude_comment_id=data.comment_id,
            )
            if prior:
                previous_comments = "\n".join(
                    f"- {row.message}" + (f" → {row.reply_text}" if row.reply_text else "")
                    for row in prior
                )
        return memory_context, previous_comments

    async def _record_failure(self, data: CommentCreate, error: str) -> None:
        async with self._session_factory() as session:
            await PendingReplyRepository(session).record_failure(
                EVENT_TYPE,
                data.comment_id,
                error,
            )
            await session.commit()
