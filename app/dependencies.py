"""FastAPI dependency injection providers."""

from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.database import get_db_session, get_session_factory
from app.services.account_service import AccountService
from app.services.ai_service import AIService
from app.services.comment_processor import CommentProcessor
from app.services.comment_repository import CommentRepository
from app.services.comment_service import CommentService
from app.services.conversation_service import ConversationService
from app.services.event_dispatcher import EventDispatcher
from app.services.gemini_service import GeminiService
from app.services.instagram_service import InstagramService
from app.services.knowledge_service import KnowledgeService
from app.services.mention_service import MentionService
from app.services.message_processor import MessageProcessor
from app.services.message_service import MessageService


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


def get_ai_service() -> AIService:
    settings = get_settings()
    return AIService(
        settings,
        get_gemini_service(),
        get_knowledge_service(),
        get_conversation_service(),
    )


def get_comment_service() -> CommentService:
    settings = get_settings()
    return CommentService(
        settings,
        get_session_factory(settings),
        get_account_service(),
        get_ai_service(),
        get_conversation_service(),
    )


def get_message_service() -> MessageService:
    settings = get_settings()
    return MessageService(
        settings,
        get_session_factory(settings),
        get_account_service(),
        get_ai_service(),
        get_conversation_service(),
    )


def get_mention_service() -> MentionService:
    settings = get_settings()
    return MentionService(
        settings,
        get_session_factory(settings),
        get_account_service(),
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
    )


def get_comment_processor() -> CommentProcessor:
    return CommentProcessor(get_comment_service())


def get_message_processor() -> MessageProcessor:
    return MessageProcessor(get_message_service())


async def get_comment_repository(
    session: AsyncSession = Depends(get_db_session),
) -> CommentRepository:
    return CommentRepository(session)
