"""FastAPI dependency injection providers."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.database import get_db_session, get_session_factory
from app.services.account_service import AccountService
from app.services.ai_service import AIService
from app.services.comment_processor import CommentProcessor
from app.services.comment_repository import CommentRepository
from app.services.comment_service import CommentService
from app.services.config_sync_service import ConfigSyncService
from app.services.conversation_service import ConversationService
from app.services.event_dispatcher import EventDispatcher
from app.services.gemini_service import GeminiService
from app.services.instagram_service import InstagramService
from app.services.knowledge_service import KnowledgeService
from app.services.mention_service import MentionService
from app.services.message_processor import MessageProcessor
from app.services.message_service import MessageService
from app.services.profile_config_store import ProfileConfigStore
from app.services.profile_resolver import ProfileResolver

_profile_config_store = ProfileConfigStore()
_config_sync_service: Optional[ConfigSyncService] = None


def get_profile_config_store() -> ProfileConfigStore:
    return _profile_config_store


@lru_cache
def get_gemini_service() -> GeminiService:
    return GeminiService(get_settings())


@lru_cache
def get_instagram_service() -> InstagramService:
    return InstagramService(get_settings())


@lru_cache
def get_knowledge_service() -> KnowledgeService:
    return KnowledgeService()


@lru_cache
def get_conversation_service() -> ConversationService:
    return ConversationService()


def get_account_service() -> AccountService:
    settings = get_settings()
    return AccountService(settings, get_session_factory(settings))


def get_profile_resolver() -> ProfileResolver:
    return ProfileResolver(get_profile_config_store(), get_account_service())


def get_ai_service() -> AIService:
    settings = get_settings()
    return AIService(
        settings,
        get_knowledge_service(),
        get_conversation_service(),
    )


def get_comment_service() -> CommentService:
    settings = get_settings()
    return CommentService(
        settings,
        get_session_factory(settings),
        get_profile_resolver(),
        get_ai_service(),
        get_conversation_service(),
    )


def get_message_service() -> MessageService:
    settings = get_settings()
    return MessageService(
        settings,
        get_session_factory(settings),
        get_profile_resolver(),
        get_ai_service(),
        get_conversation_service(),
    )


def get_mention_service() -> MentionService:
    settings = get_settings()
    return MentionService(
        settings,
        get_session_factory(settings),
        get_profile_resolver(),
        get_ai_service(),
        get_conversation_service(),
    )


def get_event_dispatcher() -> EventDispatcher:
    settings = get_settings()
    return EventDispatcher(
        settings,
        get_comment_service(),
        get_message_service(),
        get_mention_service(),
        profile_config_store=get_profile_config_store(),
    )


def get_comment_processor() -> CommentProcessor:
    return CommentProcessor(get_comment_service())


def get_message_processor() -> MessageProcessor:
    return MessageProcessor(get_message_service())


def get_config_sync_service() -> ConfigSyncService:
    global _config_sync_service
    if _config_sync_service is None:
        settings = get_settings()
        _config_sync_service = ConfigSyncService(
            settings,
            get_profile_config_store(),
            get_account_service(),
        )
    return _config_sync_service


async def get_comment_repository(
    session: AsyncSession = Depends(get_db_session),
) -> CommentRepository:
    return CommentRepository(session)
