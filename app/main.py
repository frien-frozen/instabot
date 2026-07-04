"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.config import get_settings
from app.database import close_db, get_engine, run_alembic_migrations
from app.middleware import RequestLoggingMiddleware
from app.dependencies import get_retry_service
from app.routes import health_router, webhook_router
from app.services.gemini_service import GeminiAPIError, GeminiService
from app.services.instagram_service import InstagramAPIError, InstagramService
from app.utils.logging import get_logger, log_event, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown lifecycle hooks."""
    settings = get_settings()
    setup_logging(settings)

    try:
        run_alembic_migrations()
        log_event(logger, logging.INFO, "database_migrations_applied")
    except RuntimeError as exc:
        log_event(logger, logging.ERROR, "database_migration_failed", error=str(exc))
        raise

    get_engine(settings)

    # Validate Instagram token at startup — surfaces code 190 immediately in logs
    try:
        ig = InstagramService(settings)
        profile = await ig.validate_token()
        log_event(
            logger,
            logging.INFO,
            "instagram_token_valid",
            graph_host=settings.meta_graph_host,
            user_id=profile.get("user_id") or profile.get("id"),
            username=profile.get("username"),
        )
    except InstagramAPIError as exc:
        log_event(
            logger,
            logging.ERROR,
            "instagram_token_invalid",
            graph_host=settings.meta_graph_host,
            error_code=exc.error_code,
            error_detail=str(exc),
            hint="Regenerate token in Meta dashboard → Generate token, then update META_ACCESS_TOKEN on Render",
        )

    try:
        gemini = GeminiService(settings)
        test_reply = await gemini.validate_model()
        log_event(
            logger,
            logging.INFO,
            "gemini_model_valid",
            model=gemini.model,
            configured_model=gemini.configured_model,
            test_reply=test_reply,
        )
    except GeminiAPIError as exc:
        log_event(
            logger,
            logging.ERROR,
            "gemini_model_invalid",
            model=exc.model,
            error=str(exc),
            hint="Set GEMINI_MODEL=gemini-2.5-flash in Render environment variables",
        )

    try:
        retry_service = get_retry_service()
        await retry_service.process_pending_on_startup()
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "pending_replies_replay_failed",
            error=str(exc),
        )

    yield

    await close_db()


def create_app() -> FastAPI:
    """Application factory — supports testing and future multi-tenant config."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="AI-powered Instagram comment auto-reply SaaS",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(health_router)
    app.include_router(webhook_router)

    return app


app = create_app()
