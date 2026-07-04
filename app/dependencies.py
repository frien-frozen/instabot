"""FastAPI dependency injection providers."""

from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.database import get_db_session, get_session_factory
from app.services.comment_processor import CommentProcessor
from app.services.comment_repository import CommentRepository
from app.services.gemini_service import GeminiService
from app.services.instagram_service import InstagramService
from app.services.mention_processor import MentionProcessor
from app.services.message_processor import MessageProcessor
from app.services.retry_service import RetryService


@lru_cache
def get_gemini_service() -> GeminiService:
    return GeminiService(get_settings())


@lru_cache
def get_instagram_service() -> InstagramService:
    return InstagramService(get_settings())


def get_comment_processor() -> CommentProcessor:
    settings = get_settings()
    return CommentProcessor(
        settings=settings,
        session_factory=get_session_factory(settings),
        gemini_service=get_gemini_service(),
        instagram_service=get_instagram_service(),
    )


def get_message_processor() -> MessageProcessor:
    settings = get_settings()
    return MessageProcessor(
        settings=settings,
        session_factory=get_session_factory(settings),
        gemini_service=get_gemini_service(),
        instagram_service=get_instagram_service(),
    )


def get_mention_processor() -> MentionProcessor:
    settings = get_settings()
    return MentionProcessor(
        settings=settings,
        session_factory=get_session_factory(settings),
        gemini_service=get_gemini_service(),
        instagram_service=get_instagram_service(),
    )


def get_retry_service() -> RetryService:
    settings = get_settings()
    return RetryService(
        settings=settings,
        session_factory=get_session_factory(settings),
        comment_processor=get_comment_processor(),
        message_processor=get_message_processor(),
        mention_processor=get_mention_processor(),
    )


async def get_comment_repository(
    session: AsyncSession = Depends(get_db_session),
) -> CommentRepository:
    return CommentRepository(session)
