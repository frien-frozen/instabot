"""Account-aware AI reply generation."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.instagram_account import InstagramAccount
from app.services.conversation_service import ConversationService
from app.services.gemini_service import GeminiAPIError, GeminiService
from app.services.knowledge_service import KnowledgeService
from app.utils.logging import get_logger

logger = get_logger(__name__)


class AIService:
    """Generate replies using account system prompt, knowledge, and memory."""

    def __init__(
        self,
        settings: Settings,
        gemini_service: GeminiService,
        knowledge_service: KnowledgeService,
        conversation_service: ConversationService,
    ) -> None:
        self._settings = settings
        self._gemini = gemini_service
        self._knowledge = knowledge_service
        self._conversation = conversation_service

    async def generate_reply(
        self,
        session: AsyncSession,
        account: InstagramAccount,
        user_text: str,
        *,
        user_id: str | None = None,
        account_external_id: str | None = None,
    ) -> str:
        knowledge_ctx = await self._knowledge.build_context(session, account.id)
        history_ctx = None
        if user_id and account_external_id:
            history_ctx = await self._conversation.build_history(
                session,
                account_id=account.id,
                account_external_id=account_external_id,
                user_id=user_id,
            )

        memory_parts = [part for part in (knowledge_ctx, history_ctx) if part]
        memory_context = "\n\n".join(memory_parts) if memory_parts else None

        model = account.gemini_model or self._settings.gemini_model
        original_model = self._gemini.model
        if model and model != original_model:
            self._gemini._model = model  # noqa: SLF001 — per-account model override

        try:
            return await self._gemini.generate_reply(
                user_text,
                personality_override=account.system_prompt,
                memory_context=memory_context,
            )
        finally:
            self._gemini._model = original_model  # noqa: SLF001
