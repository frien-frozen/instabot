"""Account-aware AI reply generation."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.schemas.agent_config import AgentProfileConfig
from app.services.conversation_service import ConversationService
from app.services.gemini_service import DEFAULT_SYSTEM_PROMPT, GeminiAPIError, GeminiService
from app.services.knowledge_service import KnowledgeService
from app.utils.logging import get_logger

logger = get_logger(__name__)


class AIService:
    """Generate replies using dashboard-synced prompts, knowledge, and memory."""

    def __init__(
        self,
        settings: Settings,
        knowledge_service: KnowledgeService,
        conversation_service: ConversationService,
    ) -> None:
        self._settings = settings
        self._knowledge = knowledge_service
        self._conversation = conversation_service

    async def generate_reply(
        self,
        session: AsyncSession,
        profile: AgentProfileConfig,
        user_text: str,
        *,
        user_id: str | None = None,
        account_external_id: str | None = None,
    ) -> str:
        if profile.ai_provider != "gemini":
            raise GeminiAPIError(
                f"Unsupported AI provider: {profile.ai_provider}",
                model=profile.gemini_model or self._settings.gemini_model,
            )

        knowledge_ctx = await self._knowledge.build_context(session, profile.account_id)
        history_ctx = None
        if user_id and account_external_id:
            history_ctx = await self._conversation.build_history(
                session,
                account_id=profile.account_id,
                account_external_id=account_external_id,
                user_id=user_id,
            )

        memory_parts = [part for part in (knowledge_ctx, history_ctx) if part]
        memory_context = "\n\n".join(memory_parts) if memory_parts else None

        api_key = profile.ai_api_key or self._settings.gemini_api_key
        model = profile.gemini_model or self._settings.gemini_model
        gemini = GeminiService.for_profile(self._settings, api_key, model)
        system_prompt = profile.system_prompt.strip() or DEFAULT_SYSTEM_PROMPT

        return await gemini.generate_reply(
            user_text,
            personality_override=system_prompt,
            memory_context=memory_context,
        )
