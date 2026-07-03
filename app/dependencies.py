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


@lru_cache
def get_gemini_service() -> GeminiService:
    """Singleton Gemini service instance."""
    return GeminiService(get_settings())


@lru_cache
def get_instagram_service() -> InstagramService:
    """Singleton Instagram service instance."""
    return InstagramService(get_settings())


def get_comment_processor() -> CommentProcessor:
    """Build the comment processing pipeline with injected dependencies."""
    settings = get_settings()
    return CommentProcessor(
        settings=settings,
        session_factory=get_session_factory(settings),
        gemini_service=get_gemini_service(),
        instagram_service=get_instagram_service(),
    )


async def get_comment_repository(
    session: AsyncSession = Depends(get_db_session),
) -> CommentRepository:
    """Provide a comment repository bound to the current request session."""
    return CommentRepository(session)
