"""Comment auto-reply task handler."""

from __future__ import annotations

import asyncio
import logging
import random

from app.models.event import Event
from app.models.task import Task
from app.schemas import CommentCreate
from app.services.comment_repository import CommentRepository
from app.tasks.handlers.base import BaseTaskHandler, HandlerContext
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

        delay_min = int(cfg.get("delay_min", ctx.settings.reply_delay_min_seconds))
        delay_max = int(cfg.get("delay_max", ctx.settings.reply_delay_max_seconds))
        await asyncio.sleep(random.randint(delay_min, delay_max))

        fixed = cfg.get("fixed_reply")
        if fixed and not cfg.get("ai_enabled", True):
            reply_text = str(fixed)
        else:
            profile_context = None
            if ctx.settings.profile_context_enabled and data.from_id:
                profile = await ig.fetch_user_profile(data.from_id, fallback_username=data.username)
                profile_context = format_profile_context(profile) or None
            reply_text = await ctx.gemini.generate_reply(
                data.message,
                personality_override=ctx.settings.resolved_system_prompt or None,
                profile_context=profile_context,
            )

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
            gemini_model=ctx.gemini.model,
        )
