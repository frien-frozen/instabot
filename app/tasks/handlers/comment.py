"""Comment auto-reply task handler with full post/campaign context."""

from __future__ import annotations

import asyncio
import logging
import random

from app.models.event import Event
from app.models.media import Media
from app.models.task import Task
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.media_repository import MediaRepository
from app.schemas import CommentCreate
from app.services.comment_repository import CommentRepository
from app.services.message_repository import MessageRepository
from app.tasks.handlers.base import BaseTaskHandler, HandlerContext
from app.utils.comment_context import (
    CampaignPlan,
    build_campaign_followup_dm,
    build_comment_context_package,
    classify_post_intent,
    extract_caption_triggers,
)
from app.utils.comment_intent import (
    classify_comment_intent_fast,
    pick_supportive_reply,
)
from app.utils.logging import get_logger, log_event
from app.utils.profile_context import format_profile_context
from app.utils.spam import is_spam

logger = get_logger(__name__)


class CommentTaskHandler(BaseTaskHandler):
    async def handle(self, ctx: HandlerContext, task: Task, event: Event) -> None:
        cfg = task.settings
        data = CommentCreate(**event.payload)
        ig = ctx.instagram
        auth_id = await ig.get_authenticated_user_id()

        try:
            details = await ig.fetch_comment_details(data.comment_id)
            from_obj = details.get("from") or {}
            if isinstance(from_obj, dict):
                if from_obj.get("id"):
                    data.from_id = str(from_obj["id"])
                if from_obj.get("username"):
                    data.username = str(from_obj["username"])
                if details.get("text"):
                    data.message = str(details["text"])
            media_obj = details.get("media") or {}
            if isinstance(media_obj, dict) and media_obj.get("id") and not data.media_id:
                data.media_id = str(media_obj["id"])
        except Exception as exc:
            log_event(logger, logging.WARNING, "comment_fetch_skipped", comment_id=data.comment_id, error=str(exc))

        if cfg.get("ignore_own_comments", True) and data.from_id == auth_id:
            return

        spam, reason = is_spam(data.message)
        if spam:
            log_event(logger, logging.INFO, "spam_skipped", comment_id=data.comment_id, reason=reason)
            return

        async with ctx.session_factory() as session:
            repo = CommentRepository(session)
            if await repo.has_been_replied(data.comment_id):
                return
            await repo.create(data)
            await session.commit()

        media = await self._get_or_fetch_media(ctx, data.media_id)
        campaign = await self._resolve_campaign(ctx, data.message, data.media_id, media)

        delay_min = int(cfg.get("delay_min", ctx.settings.reply_delay_min_seconds))
        delay_max = int(cfg.get("delay_max", ctx.settings.reply_delay_max_seconds))
        await asyncio.sleep(random.randint(delay_min, delay_max))

        # Campaign CTA hit → deterministic public reply + DM (no Gemini guessing).
        if campaign is not None:
            await self._handle_campaign(ctx, data, campaign)
            return

        comment_intent = await self._classify_intent(ctx, data, media)

        # Classified spam after soft filters → skip silently.
        if comment_intent == "Spam":
            log_event(
                logger,
                logging.INFO,
                "comment_intent_spam_skipped",
                comment_id=data.comment_id,
                message=data.message,
            )
            return

        # Supportive = human thanks only. Never sell / DM / lead-collect.
        if comment_intent == "Supportive":
            reply_text = pick_supportive_reply(data.message)
            await ig.reply_comment(data.comment_id, reply_text)
            async with ctx.session_factory() as session:
                repo = CommentRepository(session)
                await repo.mark_replied(data.comment_id, reply_text)
                await session.commit()
            log_event(
                logger,
                logging.INFO,
                "task_comment_supportive_reply",
                task_id=task.id,
                event_id=event.event_id,
                comment_id=data.comment_id,
                intent=comment_intent,
                reply_text=reply_text,
            )
            return

        fixed = cfg.get("fixed_reply")
        if fixed and not cfg.get("ai_enabled", True):
            reply_text = str(fixed)
        else:
            reply_text = await self._ai_reply(ctx, data, media, comment_intent=comment_intent)

        await ig.reply_comment(data.comment_id, reply_text)

        async with ctx.session_factory() as session:
            repo = CommentRepository(session)
            await repo.mark_replied(data.comment_id, reply_text)
            await session.commit()

        log_event(
            logger,
            logging.INFO,
            "task_comment_reply_sent",
            task_id=task.id,
            event_id=event.event_id,
            comment_id=data.comment_id,
            media_id=data.media_id,
            post_intent=media.intent if media else None,
            comment_intent=comment_intent,
            gemini_model=ctx.gemini.model,
        )

    async def _classify_intent(
        self,
        ctx: HandlerContext,
        data: CommentCreate,
        media: Media | None,
    ) -> str:
        fast = classify_comment_intent_fast(data.message)
        if fast is not None:
            log_event(
                logger,
                logging.INFO,
                "comment_intent_fast",
                comment_id=data.comment_id,
                intent=fast,
                message=data.message,
            )
            return fast

        post_context = None
        if media is not None:
            post_context = (
                f"Post caption:\n{media.caption or '(none)'}\n"
                f"Post intent: {media.intent}"
            )
        return await ctx.gemini.classify_comment_intent(
            data.message,
            post_context=post_context,
        )

    async def _get_or_fetch_media(self, ctx: HandlerContext, media_id: str) -> Media | None:
        if not media_id:
            return None

        async with ctx.session_factory() as session:
            cached = await MediaRepository(session).get_by_media_id(media_id)
            if cached is not None:
                return cached

        try:
            payload = await ctx.instagram.get_media(media_id)
        except Exception as exc:
            log_event(logger, logging.WARNING, "media_fetch_failed", media_id=media_id, error=str(exc))
            return None

        caption = str(payload.get("caption") or "")
        intent = classify_post_intent(caption)
        async with ctx.session_factory() as session:
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
        ctx: HandlerContext,
        comment_text: str,
        media_id: str,
        media: Media | None,
    ) -> CampaignPlan | None:
        async with ctx.session_factory() as session:
            matched = await CampaignRepository(session).match_comment(
                comment_text=comment_text,
                media_id=media_id or None,
            )
            if matched is not None:
                return CampaignPlan.from_document(matched)

        # Auto lead-magnet: caption asked for a keyword and comment matches it.
        if media and media.caption:
            triggers = extract_caption_triggers(media.caption)
            text = (comment_text or "").strip().lower()
            for trigger in triggers:
                # Exact keyword match only (e.g. "Tiklanish" / "ANALIZ") —
                # longer questions go to Gemini with full post context.
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

    async def _handle_campaign(
        self,
        ctx: HandlerContext,
        data: CommentCreate,
        campaign: CampaignPlan,
    ) -> None:
        public_reply = (campaign.public_reply or "Rahmat! DM'ingizga yubordik 📩").strip()
        await ctx.instagram.reply_comment(data.comment_id, public_reply)

        dm_text = build_campaign_followup_dm(campaign)
        if dm_text and data.from_id:
            try:
                await ctx.instagram.send_private_reply_to_comment(data.comment_id, dm_text)
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "campaign_private_reply_failed",
                    comment_id=data.comment_id,
                    error=str(exc),
                )
                try:
                    await ctx.instagram.send_message(data.from_id, dm_text)
                except Exception as exc2:
                    log_event(
                        logger,
                        logging.ERROR,
                        "campaign_dm_failed",
                        comment_id=data.comment_id,
                        error=str(exc2),
                    )

        async with ctx.session_factory() as session:
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

    async def _ai_reply(
        self,
        ctx: HandlerContext,
        data: CommentCreate,
        media: Media | None,
        *,
        comment_intent: str | None = None,
    ) -> str:
        profile_context = None
        display_name = ""
        if ctx.settings.profile_context_enabled and data.from_id:
            profile = await ctx.instagram.fetch_user_profile(
                data.from_id,
                fallback_username=data.username,
            )
            display_name = str(profile.get("name") or "")
            profile_context = format_profile_context(profile) or None

        memory_context = None
        previous_comments = None
        bot_id = await ctx.instagram.get_authenticated_user_id()
        async with ctx.session_factory() as session:
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
                            limit=ctx.settings.dm_history_limit,
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

        post_context = build_comment_context_package(
            media,
            data.message,
            data.comment_id,
            username=data.username,
            display_name=display_name,
            from_id=data.from_id,
            memory_context=memory_context,
            previous_comments=previous_comments,
            comment_intent=comment_intent,
        )

        return await ctx.gemini.generate_reply(
            data.message,
            personality_override=ctx.settings.resolved_system_prompt or None,
            profile_context=profile_context,
            memory_context=memory_context,
            post_context=post_context,
        )
