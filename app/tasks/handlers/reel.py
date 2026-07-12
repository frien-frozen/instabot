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
from app.utils.profile_context import format_profile_context
from app.utils.spam import is_spam

logger = get_logger(__name__)

QUESTION_HINTS = ("?", "how", "what", "why", "when", "where", "can", "qanday", "nima", "qayerda")


class ReelEngagementHandler(BaseTaskHandler):
    async def handle(self, ctx: HandlerContext, task: Task, event: Event) -> None:
        cfg = task.settings
        data = CommentCreate(**event.payload)
        ig = ctx.instagram
        auth_id = await ig.get_authenticated_user_id()

        if data.from_id == auth_id:
            return

        spam, _ = is_spam(data.message)
        if spam:
            return

        if not self._looks_like_question(data.message):
            log_event(logger, logging.INFO, "reel_skip_not_question", comment_id=data.comment_id)
            return

        user_id = data.from_id
        if not user_id:
            return

        profile = await ig.fetch_user_profile(user_id, fallback_username=data.username)
        requirements_ok, gate_msg = await self._check_requirements(ig, cfg, profile)
        if not requirements_ok:
            await ig.reply_comment(data.comment_id, gate_msg)
            return

        profile_context = format_profile_context(profile) or None

        public_mode = cfg.get("public_reply_mode", "ai")
        if public_mode == "fixed" and cfg.get("public_reply_fixed"):
            public_reply = str(cfg["public_reply_fixed"])
        else:
            public_reply = await ctx.gemini.generate_reply(
                data.message,
                personality_override=ctx.settings.resolved_system_prompt or None,
                profile_context=profile_context,
            )

        await ig.reply_comment(data.comment_id, public_reply)

        dm_mode = cfg.get("dm_mode", "ai")
        if dm_mode == "fixed" and cfg.get("dm_fixed"):
            dm_text = str(cfg["dm_fixed"])
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
                max_output_tokens=256,
            )

        await ig.send_message(user_id, dm_text)

        async with ctx.session_factory() as session:
            repo = CommentRepository(session)
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
            gemini_model=ctx.gemini.model,
        )

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        lower = text.lower().strip()
        if "?" in lower:
            return True
        return any(hint in lower for hint in QUESTION_HINTS)

    async def _check_requirements(self, ig, cfg: dict, profile: dict) -> tuple[bool, str]:
        gate = cfg.get("gate_message", "Follow the page and like this post first 🙌")
        require_follow = cfg.get("require_follow", False)
        require_like = cfg.get("require_like", False)

        if require_follow and not profile.get("is_user_follow_business"):
            return False, gate

        if require_like:
            # Instagram Graph API does not expose per-user like status reliably.
            # We treat commenting as engagement; gate only if follow also required and missing.
            pass

        return True, gate

    @staticmethod
    def extract_shortcode(url: str) -> str | None:
        match = re.search(r"instagram\.com/(?:reel|p|tv)/([A-Za-z0-9_-]+)", url)
        return match.group(1) if match else None
