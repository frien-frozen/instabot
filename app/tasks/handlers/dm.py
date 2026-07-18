"""DM auto-reply task handler."""

from __future__ import annotations

import asyncio
import logging
import random

from app.models.event import Event
from app.models.task import Task
from app.schemas import MessageCreate
from app.services.google_sheets_service import GoogleSheetsService
from app.services.lead_service import LeadService, conversation_may_contain_lead
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
            conversation_id = conversation.id
            username = conversation.username

        profile_context = None
        profile_username = username
        if cfg.get("profile_context_enabled", ctx.settings.profile_context_enabled):
            profile = await ig.fetch_user_profile(recipient)
            profile_context = format_profile_context(profile) or None
            profile_username = str(profile.get("username") or username or "") or username

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
            if profile_username and not conversation.username:
                conversation.username = profile_username
                await conversation.save()
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

        # Additive CRM export: Gemini extracts JSON → backend validates → Sheets.
        await self._maybe_export_lead(
            ctx,
            recipient=recipient,
            username=profile_username,
            conversation_id=conversation_id if isinstance(conversation_id, int) else None,
            account_id=data.account_id,
            memory=memory or "",
            latest_text=data.text,
        )

    async def _maybe_export_lead(
        self,
        ctx: HandlerContext,
        *,
        recipient: str,
        username: str | None,
        conversation_id: int | None,
        account_id: str | None,
        memory: str,
        latest_text: str,
    ) -> None:
        combined = f"{memory}\n{latest_text}"
        if not conversation_may_contain_lead(combined):
            return

        try:
            payload = await ctx.gemini.extract_lead(
                conversation_history=memory,
                latest_message=latest_text,
                instagram_username=username,
            )
        except Exception as exc:
            log_event(logger, logging.WARNING, "lead_extract_call_failed", error=str(exc))
            return

        if not payload.get("lead_collected"):
            return

        link = f"https://ig.me/{username}" if username else ""
        try:
            async with ctx.session_factory() as session:
                sheets = GoogleSheetsService(ctx.settings)
                lead = await LeadService(session, sheets).process_extraction(
                    payload,
                    instagram_user_id=recipient,
                    instagram_username=username,
                    conversation_id=conversation_id,
                    account_id=account_id,
                    conversation_link=link,
                )
                await session.commit()
                if lead is not None:
                    log_event(
                        logger,
                        logging.INFO,
                        "lead_pipeline_done",
                        lead_id=lead.id,
                        exported=lead.exported_to_sheets,
                        user_id=recipient,
                    )
        except Exception as exc:
            # Never fail the DM reply path because CRM export broke.
            log_event(logger, logging.ERROR, "lead_pipeline_failed", error=str(exc), user_id=recipient)
