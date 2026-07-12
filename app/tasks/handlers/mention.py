"""Mention reply task handler."""

from __future__ import annotations

import logging

from app.models.event import Event, EventType
from app.models.task import Task
from app.schemas import MentionCreate
from app.services.processed_webhook_repository import ProcessedWebhookRepository
from app.tasks.handlers.base import BaseTaskHandler, HandlerContext
from app.utils.logging import get_logger, log_event
from app.utils.profile_context import format_profile_context

logger = get_logger(__name__)


class MentionTaskHandler(BaseTaskHandler):
    async def handle(self, ctx: HandlerContext, task: Task, event: Event) -> None:
        payload = dict(event.payload)
        if event.event_type == EventType.STORY_MENTION:
            payload["mention_type"] = "story_mentions"
        data = MentionCreate(**payload)
        ig = ctx.instagram
        auth_id = await ig.get_authenticated_user_id()

        if data.from_id and data.from_id == auth_id:
            return

        async with ctx.session_factory() as session:
            if await ProcessedWebhookRepository(session).is_processed("mention", data.mention_id):
                return

        if data.mention_type != "story_mentions" and data.comment_id:
            try:
                details = await ig.get_comment(data.comment_id)
                data.comment_id = str(details.get("id") or data.comment_id)
                if details.get("text"):
                    data.text = str(details["text"])
                from_obj = details.get("from") or {}
                if isinstance(from_obj, dict):
                    if from_obj.get("id"):
                        data.from_id = str(from_obj["id"])
                    if from_obj.get("username"):
                        data.username = str(from_obj["username"])
            except Exception as exc:
                log_event(logger, logging.WARNING, "mention_enrich_failed", mention_id=data.mention_id, error=str(exc))

        profile_context = None
        if ctx.settings.profile_context_enabled and data.from_id:
            profile = await ig.fetch_user_profile(data.from_id, fallback_username=data.username)
            profile_context = format_profile_context(profile) or None

        incoming = data.text or f"Mention ({data.mention_type})"
        reply_text = await ctx.gemini.generate_reply(
            incoming,
            personality_override=ctx.settings.resolved_system_prompt or None,
            profile_context=profile_context,
        )

        if data.mention_type == "story_mentions":
            if not data.from_id:
                raise ValueError("Story mention missing sender id")
            await ig.send_message(data.from_id, reply_text)
        elif data.comment_id:
            try:
                await ig.send_private_reply_to_comment(data.comment_id, reply_text)
            except Exception:
                await ig.reply_comment(data.comment_id, reply_text)
        elif data.from_id:
            await ig.send_message(data.from_id, reply_text)
        else:
            raise ValueError("Mention has no reply target")

        async with ctx.session_factory() as session:
            await ProcessedWebhookRepository(session).mark_processed("mention", data.mention_id)
            await session.commit()

        log_event(
            logger,
            logging.INFO,
            "task_mention_reply_sent",
            task_id=task.id,
            event_id=event.event_id,
            mention_id=data.mention_id,
            gemini_model=ctx.gemini.model,
        )
