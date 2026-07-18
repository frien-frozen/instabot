"""DM auto-reply task handler."""

from __future__ import annotations

import asyncio
import logging
import random

from app.models.event import Event
from app.models.task import Task
from app.schemas import MessageCreate
from app.services.message_repository import MessageRepository
from app.tasks.handlers.base import BaseTaskHandler, HandlerContext
from app.utils.logging import get_logger, log_event
from app.utils.profile_context import format_profile_context

logger = get_logger(__name__)


class DmTaskHandler(BaseTaskHandler):
    async def handle(self, ctx: HandlerContext, task: Task, event: Event) -> None:
        cfg = task.settings
        if not cfg.get("ai_enabled", True):
            return

        data = MessageCreate(**event.payload)
        ig = ctx.instagram
        auth_id = await ig.get_authenticated_user_id()

        if data.is_echo or data.sender_id == auth_id:
            return

        recipient = data.sender_id
        if not recipient or recipient == auth_id:
            raise ValueError(f"Invalid DM recipient: {recipient}")

        delay_min = int(cfg.get("delay_min", 0))
        delay_max = int(cfg.get("delay_max", 0))
        if delay_max > 0:
            await asyncio.sleep(random.randint(delay_min, delay_max))

        async with ctx.session_factory() as session:
            repo = MessageRepository(session)
            conversation = await repo.get_or_create_conversation(
                user_id=recipient,
                account_id=data.account_id,
            )
            await repo.store_message(
                conversation,
                message_id=data.message_id,
                sender_id=data.sender_id,
                text=data.text,
                direction="incoming",
                timestamp=MessageRepository.timestamp_from_ms(data.timestamp),
            )
            memory = ""
            if cfg.get("memory_enabled", True):
                memory = await repo.build_conversation_history(
                    conversation.id,
                    bot_user_id=auth_id,
                    limit=ctx.settings.dm_history_limit,
                    exclude_message_id=data.message_id,
                )
            await session.commit()

        profile_context = None
        if cfg.get("profile_context_enabled", ctx.settings.profile_context_enabled):
            profile = await ig.fetch_user_profile(recipient)
            profile_context = format_profile_context(profile) or None

        prompt = ctx.settings.resolved_system_prompt or None
        reply_text = await ctx.gemini.generate_reply(
            data.text,
            personality_override=prompt,
            memory_context=memory or None,
            profile_context=profile_context,
            max_output_tokens=140,
        )

        result = await ig.send_message(recipient, reply_text)

        async with ctx.session_factory() as session:
            repo = MessageRepository(session)
            conversation = await repo.get_or_create_conversation(user_id=recipient, account_id=data.account_id)
            outgoing_id = str(result.get("message_id") or result.get("id") or f"out_{data.message_id}")
            await repo.store_message(
                conversation,
                message_id=outgoing_id,
                sender_id=auth_id,
                text=reply_text,
                direction="outgoing",
            )
            await repo.mark_reply_sent(data.message_id)
            await session.commit()

        log_event(
            logger,
            logging.INFO,
            "task_dm_reply_sent",
            task_id=task.id,
            event_id=event.event_id,
            recipient_id=recipient,
            gemini_model=ctx.gemini.model,
        )
