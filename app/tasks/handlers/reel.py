"""Reel / post engagement automation handler."""

from __future__ import annotations

import logging
import re

from app.models.event import Event
from app.models.task import Task
from app.schemas import CommentCreate
from app.services.comment_repository import CommentRepository
from app.tasks.handlers.base import BaseTaskHandler, HandlerContext
from app.utils.logging import get_logger, log_event
from app.utils.organ_trade_safety import (
    ORGAN_TRADE_REFUSAL_REPLY,
    REDACTED_ORGAN_TRADE_TEXT,
    is_illegal_organ_trade_intent,
    log_illegal_organ_trade_attempt,
)
from app.utils.profile_context import format_profile_context
from app.utils.spam import is_spam

logger = get_logger(__name__)


class ReelEngagementHandler(BaseTaskHandler):
    """
    Per-media automation: public reply + DM for comments on a specific Reel/post.

    Fixed-mode replies fire on every non-spam comment (emoji, keyword, question).
    AI mode also replies to every comment (short), since this task replaces the
    generic comment auto-reply for that media.
    """

    async def handle(self, ctx: HandlerContext, task: Task, event: Event) -> None:
        cfg = task.settings
        data = CommentCreate(**event.payload)
        ig = ctx.instagram
        auth_id = await ig.get_authenticated_user_id()

        if data.from_id and data.from_id == auth_id:
            return

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
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "reel_comment_fetch_skipped",
                comment_id=data.comment_id,
                error=str(exc),
            )

        if data.from_id and data.from_id == auth_id:
            return

        spam, _ = is_spam(data.message)
        if spam:
            return

        async with ctx.session_factory() as session:
            repo = CommentRepository(session)
            if await repo.has_been_replied(data.comment_id):
                return

        if is_illegal_organ_trade_intent(data.message):
            log_illegal_organ_trade_attempt(instagram_user_id=data.from_id)
            await ig.reply_comment(data.comment_id, ORGAN_TRADE_REFUSAL_REPLY)
            async with ctx.session_factory() as session:
                safe = data.model_copy(update={"message": REDACTED_ORGAN_TRADE_TEXT})
                await CommentRepository(session).create(safe)
                await CommentRepository(session).mark_replied(
                    data.comment_id,
                    ORGAN_TRADE_REFUSAL_REPLY,
                )
                await session.commit()
            return

        user_id = data.from_id
        if not user_id:
            log_event(logger, logging.WARNING, "reel_skip_no_from_id", comment_id=data.comment_id)
            return

        profile = await ig.fetch_user_profile(user_id, fallback_username=data.username)
        requirements_ok, gate_msg = await self._check_requirements(ig, cfg, profile)
        if not requirements_ok:
            await ig.reply_comment(data.comment_id, gate_msg)
            async with ctx.session_factory() as session:
                await CommentRepository(session).create(data)
                await CommentRepository(session).mark_replied(data.comment_id, gate_msg)
                await session.commit()
            return

        profile_context = format_profile_context(profile) or None

        public_mode = str(cfg.get("public_reply_mode", "ai")).lower()
        public_fixed = str(cfg.get("public_reply_fixed") or "").strip()
        if public_mode == "fixed" and public_fixed:
            public_reply = public_fixed
        else:
            public_reply = await ctx.gemini.generate_reply(
                data.message,
                personality_override=ctx.settings.resolved_system_prompt or None,
                profile_context=profile_context,
                max_output_tokens=80,
            )

        await ig.reply_comment(data.comment_id, public_reply)

        dm_mode = str(cfg.get("dm_mode", "ai")).lower()
        dm_fixed = str(cfg.get("dm_fixed") or "").strip()
        if dm_mode == "fixed" and dm_fixed:
            dm_text = dm_fixed
        else:
            dm_prompt = (
                f"The user commented on your reel: \"{data.message}\"\n"
                f"You already replied publicly: \"{public_reply}\"\n"
                "Now send a friendly follow-up DM with extra helpful information."
            )
            dm_text = await ctx.gemini.generate_reply(
                dm_prompt,
                personality_override=ctx.settings.resolved_system_prompt or None,
                profile_context=profile_context,
                max_output_tokens=140,
            )

        # Private reply tied to the comment works even if user never DMed first.
        try:
            await ig.send_private_reply_to_comment(data.comment_id, dm_text)
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "reel_private_reply_failed",
                comment_id=data.comment_id,
                error=str(exc),
            )
            try:
                await ig.send_message(user_id, dm_text)
            except Exception as exc2:
                log_event(
                    logger,
                    logging.ERROR,
                    "reel_dm_failed",
                    comment_id=data.comment_id,
                    user_id=user_id,
                    error=str(exc2),
                )

        async with ctx.session_factory() as session:
            repo = CommentRepository(session)
            await repo.create(data)
            await repo.mark_replied(data.comment_id, public_reply)
            await session.commit()

        log_event(
            logger,
            logging.INFO,
            "task_reel_engagement_complete",
            task_id=task.id,
            event_id=event.event_id,
            comment_id=data.comment_id,
            user_id=user_id,
            public_mode=public_mode,
            dm_mode=dm_mode,
            gemini_model=ctx.gemini.model,
        )

    async def _check_requirements(self, ig, cfg: dict, profile: dict) -> tuple[bool, str]:
        gate = cfg.get("gate_message", "Follow the page and like this post first 🙌")
        require_follow = cfg.get("require_follow", False)

        if require_follow and not profile.get("is_user_follow_business"):
            return False, str(gate)

        return True, str(gate)

    @staticmethod
    def extract_shortcode(url: str) -> str | None:
        match = re.search(r"instagram\.com/(?:reel|p|tv)/([A-Za-z0-9_-]+)", url)
        return match.group(1) if match else None
